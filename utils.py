import numpy as np
from PIL import Image
from matplotlib import image as plt_image
import os
import pandas as pd
import glob
from random import sample, seed
import random
import torch
from torchvision import transforms
import sys
import time
from typing import List, Tuple
from xlrd.biffh import XLRDError
from zipfile import BadZipFile
from skimage.util import random_noise
from mpl_toolkits.axes_grid1 import ImageGrid
import matplotlib.pyplot as plt
from nets_mil import ResNet50_GN_GatedAttention, ReceptorNet
import nets
from math import isclose
from argparse import Namespace as argsNamespace
from shutil import copyfile
from datetime import date
import inspect
import torch.nn.functional as F
import multiprocessing
from tqdm import tqdm
from pathlib import Path
import sys
from PIL import ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True
from random import shuffle
import skimage
from fractions import Fraction


#if sys.platform == 'win32':
#    os.add_dll_directory(r'C:\ran_programs\Anaconda3\openslide_bin_ran')
import openslide

Image.MAX_IMAGE_PIXELS = None


def chunks(list: List, length: int):
    new_list = [ list[i * length:(i + 1) * length] for i in range((len(list) + length - 1) // length )]
    return new_list


def make_dir(dirname):
    if not dirname in next(os.walk(os.getcwd()))[1]:
        try:
            os.mkdir(dirname)
        except OSError:
            print('Creation of directory ', dirname, ' failed...')
            raise


def get_optimal_slide_level(slide, magnification, desired_mag, tile_size):
    desired_downsample = magnification / desired_mag  # downsample needed for each dimension (reflected by level_downsamples property)

    if desired_downsample < 1: #upsample
        best_slide_level = 0
        level_0_tile_size = int(desired_downsample * tile_size)
        adjusted_tile_size = level_0_tile_size
    else:
        level, best_next_level = -1, -1
        for index, downsample in enumerate(slide.level_downsamples):
            if isclose(desired_downsample, downsample, rel_tol=1e-3):
                level = index
                level_downsample = 1
                break

            elif downsample < desired_downsample:
                best_next_level = index
                #level_downsample = int(desired_downsample / slide.level_downsamples[best_next_level])
                level_downsample = desired_downsample / slide.level_downsamples[best_next_level] #RanS 26.12.21

        #adjusted_tile_size = tile_size * level_downsample
        adjusted_tile_size = int(tile_size * level_downsample) #RanS 26.12.21
        best_slide_level = level if level > best_next_level else best_next_level
        #level_0_tile_size = int(desired_downsample) * tile_size
        level_0_tile_size = int(desired_downsample * tile_size) #RanS 26.12.21

    return best_slide_level, adjusted_tile_size, level_0_tile_size


def _choose_data(grid_list: list,
                 slide: openslide.OpenSlide,
                 how_many: int,
                 magnification: int,
                 tile_size: int = 256,
                 print_timing: bool = False,
                 desired_mag: int = 20,
                 loan: bool = False,
                 random_shift: bool = True):
    """
    This function choose and returns data to be held by DataSet.
    The function is in the PreLoad Version. It works with slides already loaded to memory.

    :param grid_list: A list of all grids for this specific slide
    :param slide: An OpenSlide object of the slide.
    :param how_many: how_many tiles to return from the slide.
    :param magnification: The magnification of level 0 of the slide
    :param tile_size: Desired tile size from the slide at the desired magnification
    :param print_timing: Do or don't collect timing for this procedure
    :param desired_mag: Desired Magnification of the tiles/slide.
    :return:
    """

    best_slide_level, adjusted_tile_size, level_0_tile_size = get_optimal_slide_level(slide, magnification, desired_mag, tile_size)

    # Choose locations from the grid list:
    loc_num = len(grid_list)
    # FIXME: The problem of not enough tiles should disappear when we'll work with fixed tile locations + random vertiacl/horizontal movement
    try:
        idxs = sample(range(loc_num), how_many)
    except ValueError:
        raise ValueError('Requested more tiles than available by the grid list')

    locs = [grid_list[idx] for idx in idxs]
    image_tiles, time_list, labels = _get_tiles(slide=slide,
                                                locations=locs,
                                                tile_size_level_0=level_0_tile_size,
                                                adjusted_tile_sz=adjusted_tile_size,
                                                output_tile_sz=tile_size,
                                                best_slide_level=best_slide_level,
                                                print_timing=print_timing,
                                                random_shift=random_shift,
                                                loan=loan)

    return image_tiles, time_list, labels, locs


def _get_tiles(slide: openslide.OpenSlide,
               locations: List[Tuple],
               tile_size_level_0: int,
               adjusted_tile_sz: int,
               output_tile_sz: int,
               best_slide_level: int,
               print_timing: bool = False,
               random_shift: bool = False,
               oversized_HC_tiles: bool = False,
               loan: bool = False):
    """
    This function extract tiles from the slide.
    :param slide: OpenSlide object containing a slide
    :param locations: locations of te tiles to be extracted
    :param tile_size_level_0: tile size adjusted for level 0
    :param adjusted_tile_sz: tile size adjusted for best_level magnification
    :param output_tile_sz: output tile size needed
    :param best_slide_level: best slide level to get tiles from
    :param print_timing: collect time profiling data ?
    :return:
    """

    #RanS 20.12.20 - plot thumbnail with tile locations
    temp = False
    if temp:
        from matplotlib.patches import Rectangle
        import matplotlib.pyplot as plt
        level_1 = slide.level_count - 5
        ld = int(slide.level_downsamples[level_1]) #level downsample
        thumb = (slide.read_region(location=(0, 0), level=level_1, size=slide.level_dimensions[level_1])).convert('RGB')
        fig, ax = plt.subplots()
        plt.imshow(thumb)
        for idx, loc in enumerate(locations):
            print((loc[1]/ld, loc[0]/ld))
            rect = Rectangle((loc[1]/ld, loc[0]/ld), adjusted_tile_sz / ld, adjusted_tile_sz / ld, color='r', linewidth=3, fill=False)
            ax.add_patch(rect)
            #rect = Rectangle((loc[1] / ld, loc[0] / ld), tile_sz / ld, tile_sz / ld, color='g', linewidth=3, fill=False)
            #ax.add_patch(rect)

        patch1 = slide.read_region((loc[1], loc[0]), 0, (600, 600)).convert('RGB')
        plt.figure()
        plt.imshow(patch1)

        patch2 = slide.read_region((loc[1], loc[0]), 0, (2000, 2000)).convert('RGB')
        plt.figure()
        plt.imshow(patch2)

        plt.show()

    #tiles_PIL = []

    #RanS 28.4.21, preallocate list of images
    empty_image = Image.fromarray(np.uint8(np.zeros((output_tile_sz, output_tile_sz, 3))))
    tiles_PIL = [empty_image] * len(locations)

    start_gettiles = time.time()

    if oversized_HC_tiles:
        adjusted_tile_sz *= 2
        output_tile_sz *= 2
        tile_shifting = (tile_size_level_0 // 2, tile_size_level_0 // 2)

    # get localized labels - RanS 17.6.21
    labels = np.zeros(len(locations)) - 1
    if loan:
        slide_name = os.path.splitext(os.path.basename(slide._filename))[0]
        annotation_file = os.path.join(os.path.dirname(slide._filename), 'local_labels', slide_name + '-labels.png')
        # annotation = np.array(Image.open(annotation_file))
        annotation = (plt_image.imread(annotation_file) * 255).astype('uint8')
        ds = 8  # defined in the QuPath groovy script
        #Tumor = blue = (0,0,255)
        #Positive = red = (250,62,62)

    for idx, loc in enumerate(locations):
        if random_shift:
            tile_shifting = sample(range(-tile_size_level_0 // 2, tile_size_level_0 // 2), 2)
        #elif oversized_HC_tiles:
        #    tile_shifting = (tile_size_level_0 // 2, tile_size_level_0 // 2)

        if random_shift or oversized_HC_tiles:
            new_loc_init = {'Top': loc[0] - tile_shifting[0],
                            'Left': loc[1] - tile_shifting[1]}
            new_loc_end = {'Bottom': new_loc_init['Top'] + tile_size_level_0,
                           'Right': new_loc_init['Left'] + tile_size_level_0}
            if new_loc_init['Top'] < 0:
                new_loc_init['Top'] += abs(new_loc_init['Top'])
            if new_loc_init['Left'] < 0:
                new_loc_init['Left'] += abs(new_loc_init['Left'])
            if new_loc_end['Bottom'] > slide.dimensions[1]:
                delta_Height = new_loc_end['Bottom'] - slide.dimensions[1]
                new_loc_init['Top'] -= delta_Height
            if new_loc_end['Right'] > slide.dimensions[0]:
                delta_Width = new_loc_end['Right'] - slide.dimensions[0]
                new_loc_init['Left'] -= delta_Width
        else:
            new_loc_init = {'Top': loc[0],
                            'Left': loc[1]}

        try:
            '''
            # FIXME: shifting the origin by 1.5 tiles
            new_loc_init['Top'] -= (128 + 256) * round(adjusted_tile_sz/output_tile_sz)
            new_loc_init['Left'] -= (128 + 256) * round(adjusted_tile_sz/output_tile_sz)'''
            # FIXME:
            #image = slide.read_region(location=(20000, 20000), level=0, size=(4096, 4096)).convert('RGB')
            #image = slide.read_region(location=(8000, 20000), level=0, size=(4096, 4096)).convert('RGB')
            #image = Image.fromarray(np.ones([adjusted_tile_sz, adjusted_tile_sz, 3], dtype=np.uint8) * 255)
            # Locations for tiles in NEGATIVE slide: TCGA-AR-A1AI-01Z-00-DX1.5EF2A589-4284-45CF-BF0C-169E3A85530C.svs
            #image = slide.read_region(location=(46000, 23000), level=0, size=(2048, 2048)).convert('RGB')
            #image = slide.read_region(location=(49000, 33000), level=best_slide_level, size=(adjusted_tile_sz, adjusted_tile_sz)).convert('RGB')

            # When reading from OpenSlide the locations is as follows (col, row)

            image = slide.read_region((new_loc_init['Left'], new_loc_init['Top']), best_slide_level, (adjusted_tile_sz, adjusted_tile_sz)).convert('RGB')
        except:
            print('failed to read slide ' + slide._filename + ' in location ' + str(loc[1]) + ',' + str(loc[0]))
            print('taking blank patch instead')
            image = Image.fromarray(np.zeros([adjusted_tile_sz, adjusted_tile_sz, 3], dtype=np.uint8))

        # get localized labels - RanS 17.6.21
        if loan:
            d = adjusted_tile_sz // ds
            x = new_loc_init['Left'] // ds
            y = new_loc_init['Top'] // ds
            x0 = int(slide.properties[openslide.PROPERTY_NAME_BOUNDS_X]) // ds
            y0 = int(slide.properties[openslide.PROPERTY_NAME_BOUNDS_Y]) // ds

            #temp for debug
            temp_plot = False
            if temp_plot:
                fig, ax = plt.subplots()
                ds1 = 8
                q = slide.get_thumbnail((slide.dimensions[0]//(ds*ds1), slide.dimensions[1]//(ds*ds1)))
                plt.imshow(q, alpha=1)
                q1 = Image.fromarray(annotation)
                q1.thumbnail((annotation.shape[1]//ds1, annotation.shape[0]//ds1))
                seg = np.zeros((q.size[0], q.size[1], 3))
                seg[y0//ds1:y0//ds1+q1.size[1], x0//ds1:x0//ds1+q1.size[0], :] = np.array(q1)/255
                plt.imshow(seg, alpha=0.5)
                from matplotlib.patches import Rectangle
                rect = Rectangle((new_loc_init['Left']//(ds*ds1), new_loc_init['Top']//(ds*ds1)), adjusted_tile_sz//(ds*ds1), adjusted_tile_sz//(ds*ds1), edgecolor='g', facecolor='g')
                ax.add_patch(rect)
            annotation_tile = annotation[y-y0:y-y0+d, x-x0:x-x0 + d, :]
            #blue_zone = np.sum(annotation_tile[:,:,2] == 255) / (annotation_tile.size//3)
            red_zone = np.sum(annotation_tile[:, :, 0] == 250) / (annotation_tile.size // 3)
            if red_zone > 0.1:
                labels[idx] = 1
            else:
                labels[idx] = 0

        temp_plot1 = False
        if temp_plot1:
            plt.imshow(image)

        if adjusted_tile_sz != output_tile_sz:
            image = image.resize((output_tile_sz, output_tile_sz))

        tiles_PIL[idx] = image

    end_gettiles = time.time()

    if print_timing:
        time_list = [0, (end_gettiles - start_gettiles) / len(locations)]
    else:
        time_list = [0]

    return tiles_PIL, time_list, labels


def device_gpu_cpu():
    if torch.cuda.is_available():
        device = torch.device('cuda')
        print('Using CUDA')
    else:
        device = torch.device('cpu')
        print('Using cpu')

    return device


def get_cpu():
    platform = sys.platform
    if platform == 'linux':
        cpu = len(os.sched_getaffinity(0))
    elif platform == 'darwin':
        cpu = 2
        platform = 'MacOs'
    else: #windows
        cpu = multiprocessing.cpu_count()
        platform = 'Windows'

    print('Running on {} with {} CPUs'.format(platform, cpu))
    return cpu


def run_data(experiment: str = None,
             test_fold: int = 1,
             transform_type: str = 'none',
             tile_size: int = 256,
             tiles_per_bag: int = 50,
             num_bags: int = 1,
             DX: bool = False,
             DataSet_name: list = ['TCGA'],
             DataSet_test_name: list = [None],
             DataSet_size: tuple = None,
             DataSet_Slide_magnification: int = None,
             epoch: int = None,
             model: str = None,
             transformation_string: str = None,
             Receptor: str = None,
             MultiSlide: bool = False,
             test_mean_auc: float = None,
             is_per_patient: bool = False,
             is_last_layer_freeze: bool = False,
             is_repeating_data: bool = False,
             data_limit: int = None,
             free_bias: bool = False,
             carmel_only: bool = False,
             CAT_only: bool = False,
             Remark: str = '',
             Class_Relation: float = None,
             learning_rate: float = -1,
             weight_decay: float = -1,
             censored_ratio: float = -1,
             combined_loss_weights: list = [],
             receptor_tumor_mode: int = -1,
             is_domain_adaptation: bool = False):
    """
    This function writes the run data to file
    :param experiment:
    :param from_epoch:
    :param MultiSlide: Describes if tiles from different slides with same class are mixed in the same bag
    :return:
    """

    if experiment is not None:
        if sys.platform == 'linux':
            if experiment > 0 and experiment < 10000:
                # One of Ran's experiments
                user_name = 'Ran'
                run_file_name = r'/home/rschley/code/WSI_MIL/general_try4/runs/run_data.xlsx'
                location_prefix = '/home/rschley/code/WSI_MIL/general_try4/'
            elif experiment > 10000 and experiment < 20000:
                # One of Omer's experiments
                user_name = 'Omer'
                run_file_name = r'/home/womer/project/runs/run_data.xlsx'
                location_prefix = '/home/womer/project/'
            elif experiment > 20000 and experiment < 30000:
                # One of Gil's experiments
                user_name = 'Gil'
                run_file_name = r'/mnt/gipnetapp_public/sgils/ran/runs/run_data.xlsx'
                location_prefix = '/mnt/gipnetapp_public/sgils/ran/'
        else:
            user_name = None
            run_file_name = 'runs/run_data.xlsx'

    else:
        run_file_name = 'runs/run_data.xlsx'

    if sys.platform == 'win32': #Ran's laptop
        run_file_name = r'C:\WSI_MIL_runs\run_data.xlsx'

    if os.path.isfile(run_file_name):
        read_success = False
        read_attempts = 0
        while (not read_success) and (read_attempts < 10):
            try:
                run_DF = pd.read_excel(run_file_name)
                read_success = True
            except (XLRDError, BadZipFile):
                print('Couldn\'t open file {}, check if file is corrupt'.format(run_file_name))
                return
            except ValueError:
                print('run_data file is being used, retrying in 5 seconds')
                read_attempts += 1
                time.sleep(5)
        if not read_success:
            print('Couldn\'t open file {} after 10 attempts'.format(run_file_name))
            return

        try:
            run_DF.drop(labels='Unnamed: 0', axis='columns',  inplace=True)
        except KeyError:
            pass

        run_DF_exp = run_DF.set_index('Experiment', inplace=False)
    else:
        run_DF = pd.DataFrame()

    # If a new experiment is conducted:
    if experiment is None:
        if os.path.isfile(run_file_name):
            experiment = run_DF_exp.index.values.max() + 1
        else:
            experiment = 1

        location = os.path.join(os.path.abspath(os.getcwd()), 'runs', 'Exp_' + str(experiment) + '-' + Receptor + '-TestFold_' + str(test_fold))
        if type(DataSet_name) is not list:
            DataSet_name = [DataSet_name]

        if type(DataSet_test_name) is not list:
            DataSet_test_name = [DataSet_test_name]

        run_dict = {'Experiment': experiment,
                    'Start Date': str(date.today()),
                    'Test Fold': test_fold,
                    'Transformations': transform_type,
                    'Tile Size': tile_size,
                    'Tiles Per Bag': tiles_per_bag,
                    'MultiSlide Per Bag': MultiSlide,
                    'No. of Bags': num_bags,
                    'Location': location,
                    'DX': DX,
                    'DataSet': ' / '.join(DataSet_name),
                    'Test Set (DataSet)': ' / '.join(DataSet_test_name) if DataSet_test_name[0] != None else None,
                    'Receptor': Receptor,
                    'Model': 'None',
                    'Last Epoch': 0,
                    'Transformation String': 'None',
                    'Desired Slide Magnification': DataSet_Slide_magnification,
                    'Per Patient Training': is_per_patient,
                    'Last Layer Freeze': is_last_layer_freeze,
                    'Repeating Data': is_repeating_data,
                    'Data Limit': data_limit,
                    'Free Bias': free_bias,
                    'Carmel Only': carmel_only,
                    'Using Feature from CAT model alone': CAT_only,
                    'Remark': Remark,
                    'Class Relation': Class_Relation,
                    'Learning Rate': learning_rate,
                    'Weight Decay': weight_decay,
                    'Censor Ratio': censored_ratio,
                    'Combined Loss Weights': [Fraction(combined_loss_weights[item]).limit_denominator() for item in range(len(combined_loss_weights))],
                    'Receptor + is_Tumor Train Mode': receptor_tumor_mode,
                    'Trained with Domain Adaptation': is_domain_adaptation
                    }
        run_DF = run_DF.append([run_dict], ignore_index=True)
        if not os.path.isdir('runs'):
            os.mkdir('runs')

        #RanS 19.7.21, instead of in save_code_files which is buggy
        if not os.path.isdir(location):
            os.mkdir(location)

        run_DF.to_excel(run_file_name)
        print('Created a new Experiment (number {}). It will be saved at location: {}'.format(experiment, location))

        #backup for run_data
        backup_dir = os.path.join(os.path.abspath(os.getcwd()), 'runs', 'run_data_backup')
        print(backup_dir)
        if not os.path.isdir(backup_dir):
            os.mkdir(backup_dir)
            print('backup dir created')
        try:
            run_DF.to_excel(os.path.join(backup_dir, 'run_data_exp' + str(experiment) + '.xlsx'))
        except:
            raise IOError('failed to back up run_data, please check there is enough storage')

        #return location, experiment
        return {'Location': location,
                'Experiment': experiment
                }

    elif experiment is not None and epoch is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'Last Epoch'] = epoch
        run_DF.to_excel(run_file_name)

    elif experiment is not None and model is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'Model'] = model
        run_DF.to_excel(run_file_name)

    elif experiment is not None and transformation_string is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'Transformation String'] = transformation_string
        run_DF.to_excel(run_file_name)

    elif experiment is not None and DataSet_size is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'Train DataSet Size'] = DataSet_size[0]
        run_DF.at[index, 'Test DataSet Size'] = DataSet_size[1]
        run_DF.to_excel(run_file_name)

    elif experiment is not None and DataSet_Slide_magnification is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'Desired Slide Magnification'] = DataSet_Slide_magnification
        run_DF.to_excel(run_file_name)

    elif experiment is not None and test_mean_auc is not None:
        index = run_DF[run_DF['Experiment'] == experiment].index.values[0]
        run_DF.at[index, 'TestSet Mean AUC'] = test_mean_auc
        run_DF.to_excel(run_file_name)


    # In case we want to continue from a previous training session
    else:
        location = run_DF_exp.loc[[experiment], ['Location']].values[0][0]
        test_fold = int(run_DF_exp.loc[[experiment], ['Test Fold']].values[0][0])
        transformations = run_DF_exp.loc[[experiment], ['Transformations']].values[0][0] #RanS 9.12.20
        tile_size = int(run_DF_exp.loc[[experiment], ['Tile Size']].values[0][0])
        tiles_per_bag = int(run_DF_exp.loc[[experiment], ['Tiles Per Bag']].values[0][0])
        num_bags = int(run_DF_exp.loc[[experiment], ['No. of Bags']].values[0][0])
        DX = bool(run_DF_exp.loc[[experiment], ['DX']].values[0][0])
        DataSet_name = str(run_DF_exp.loc[[experiment], ['DataSet']].values[0][0])
        Receptor = str(run_DF_exp.loc[[experiment], ['Receptor']].values[0][0])
        MultiSlide = str(run_DF_exp.loc[[experiment], ['MultiSlide Per Bag']].values[0][0])
        model_name = str(run_DF_exp.loc[[experiment], ['Model']].values[0][0])
        Desired_Slide_magnification = int(run_DF_exp.loc[[experiment], ['Desired Slide Magnification']].values[0][0])
        try:
            censored_ratio = run_DF_exp.loc[[experiment], ['Censor Ratio']].values[0][0]
            combined_loss_weights = run_DF_exp.loc[[experiment], ['Combined Loss Weights']].values[0][0]
            receptor_tumor_mode = run_DF_exp.loc[[experiment], ['Receptor + is_Tumor Train Mode']].values[0][0]
            receptor_tumor_mode = convert_value_to_integer(receptor_tumor_mode)
            free_bias = bool(run_DF_exp.loc[[experiment], ['Free Bias']].values[0][0])
            CAT_only = bool(run_DF_exp.loc[[experiment], ['Using Feature from CAT model alone']].values[0][0])
            Class_Relation = float(run_DF_exp.loc[[experiment], ['Class Relation']].values[0][0])
        except:
            censored_ratio = np.nan
            combined_loss_weights = np.nan
            receptor_tumor_mode = np.nan
            receptor_tumor_mode = np.nan
            free_bias = np.nan
            CAT_only = np.nan
            Class_Relation = np.nan


        if sys.platform == 'linux':
            if location.split('/')[0] == 'runs':
                location = location_prefix + location

        '''return location, test_fold, transformations, tile_size, tiles_per_bag, num_bags,\
               DX, DataSet_name, Receptor, MultiSlide, model_name, Desired_Slide_magnification,\
               free_bias, CAT_only'''

        return {'Location': location,
                'Test Fold': test_fold,
                'Transformations': transformations,
                'Tile Size': tile_size,
                'Tiles Per Bag': tiles_per_bag,
                'Num Bags': num_bags,
                'DX': DX,
                'Dataset Name': DataSet_name,
                'Receptor': Receptor,
                'MultiSlide': MultiSlide,
                'Model Name': model_name,
                'Desired Slide Magnification': Desired_Slide_magnification,
                'Free Bias': free_bias,
                'CAT Only': CAT_only,
                'Class Relation': Class_Relation,
                'User': user_name,
                'Receptor + is_Tumor Train Mode': receptor_tumor_mode
                }


def convert_value_to_integer(value):
    return int(value) if not np.isnan(value) else -1


def run_data_multi_model(experiments: List[str] = None, models: List[str] = None,
                         epoch: int = None,  transformation_string: str = None):
    num_experiments = len(experiments)
    if experiments is not None and transformation_string is not None:
        for index in range(num_experiments):
            run_data(experiment=experiments[index], transformation_string=transformation_string)
    elif experiments is not None and models is not None:
        for index in range(num_experiments):
            run_data(experiment=experiments[index], model=models[index])
    elif experiments is not None and epoch is not None:
        for index in range(num_experiments):
            run_data(experiment=experiments[index], epoch=epoch)




def get_concat(im1, im2):
    dst = Image.new('RGB', (im1.width + im2.width, im1.height))
    dst.paste(im1, (0, 0))
    dst.paste(im2, (im1.width, 0))
    return dst


class Cutout(object):
    """Randomly mask out one or more patches from an image.
    Args:
        n_holes (int): Number of patches to cut out of each image.
        length (int): The length (in pixels) of each square patch.
    """
    def __init__(self, n_holes, length):
        self.n_holes = n_holes
        self.length = length

    def __call__(self, img):
        """
        Args:
            img (Tensor): Tensor image of size (C, H, W).
        Returns:
            Tensor: Image with n_holes of dimension length x length cut out of it.
        """
        h = img.size(1)
        w = img.size(2)

        mask = np.ones((h, w), np.float32)

        for n in range(self.n_holes):
            y = torch.randint(low=0, high=h, size=(1,)).numpy()[0]
            x = torch.randint(low=0, high=w, size=(1,)).numpy()[0]
            '''
            # Numpy random numbers will produce the same numbers in every epoch - I changed the random number producer
            # to torch.random to overcome this issue. 
            y = np.random.randint(h)            
            x = np.random.randint(w)
            '''


            y1 = np.clip(y - self.length // 2, 0, h)
            y2 = np.clip(y + self.length // 2, 0, h)
            x1 = np.clip(x - self.length // 2, 0, w)
            x2 = np.clip(x + self.length // 2, 0, w)

            mask[y1: y2, x1: x2] = 0.

        mask = torch.from_numpy(mask)
        mask = mask.expand_as(img)
        img = img * mask

        return img


class MyRotation:
    """Rotate by one of the given angles."""

    def __init__(self, angles):
        self.angles = angles

    def __call__(self, x):
        angle = random.choice(self.angles)
        return transforms.functional.rotate(x, angle)


class MyCropTransform:
    """crop the image at upper left."""

    def __init__(self, tile_size):
        self.tile_size = tile_size

    def __call__(self, x):
        #x = transforms.functional.crop(img=x, top=0, left=0, height=self.tile_size, width=self.tile_size)
        x = transforms.functional.crop(img=x, top=x.size[0] - self.tile_size, left=x.size[1] - self.tile_size, height=self.tile_size, width=self.tile_size)
        return x


class MyGaussianNoiseTransform:
    """add gaussian noise."""

    def __init__(self, sigma):
        self.sigma = sigma

    def __call__(self, x):
        #x += torch.normal(mean=np.zeros_like(x), std=self.sigma)
        stdev = self.sigma[0]+(self.sigma[1]-self.sigma[0])*np.random.rand()
        # convert PIL Image to ndarray
        x_arr = np.asarray(x)

        # random_noise() method will convert image in [0, 255] to [0, 1.0],
        # inherently it use np.random.normal() to create normal distribution
        # and adds the generated noised back to image
        noise_img = random_noise(x_arr, mode='gaussian', var=stdev ** 2)
        noise_img = (255 * noise_img).astype(np.uint8)

        x = Image.fromarray(noise_img)
        return x


class MyMeanPixelRegularization:
    """replace patch with single pixel value"""

    def __init__(self, p):
        self.p = p

    def __call__(self, x):
        if np.random.rand() < self.p:
            x = torch.zeros_like(x) + torch.tensor([[[0.87316266]], [[0.79902739]], [[0.84941472]]])
        return x


def define_transformations(transform_type, train, tile_size, color_param=0.1, norm_type='Ron'):

    MEAN = {'TCGA': [58.2069073 / 255, 96.22645279 / 255, 70.26442606 / 255],
            'HEROHE': [224.46091564 / 255, 190.67338568 / 255, 218.47883547 / 255],
            'Ron': [0.8998, 0.8253, 0.9357],
            'Imagenet': [0.485, 0.456, 0.406],
            'Amir': [0.9357, 0.8253, 0.8998]
            }

    STD = {'TCGA': [40.40400300279664 / 255, 58.90625962739444 / 255, 45.09334057330417 / 255],
           'HEROHE': [np.sqrt(1110.25292532) / 255, np.sqrt(2950.9804851) / 255, np.sqrt(1027.10911208) / 255],
           'Ron': [0.1125, 0.1751, 0.0787],
           'Imagenet': [0.229, 0.224, 0.225],
           'Amir': [0.0787, 0.1751, 0.1125]
           }

    # Setting the transformation:
    if transform_type == 'aug_receptornet':
        final_transform = transforms.Compose([transforms.Normalize(
                                                  mean=(MEAN[norm_type][0], MEAN[norm_type][1], MEAN[norm_type][2]),
                                                  std=(STD[norm_type][0], STD[norm_type][1], STD[norm_type][2]))])
    else:
        final_transform = transforms.Compose([transforms.ToTensor(),
                                              transforms.Normalize(
                                                  mean=(MEAN[norm_type][0], MEAN[norm_type][1], MEAN[norm_type][2]),
                                                  std=(STD[norm_type][0], STD[norm_type][1], STD[norm_type][2]))
                                              ])
    scale_factor = 0.2
    # if self.transform and self.train:
    if transform_type != 'none' and train:
        # TODO: Consider using - torchvision.transforms.RandomErasing(p=0.5, scale=(0.02, 0.33), ratio=(0.3, 3.3), value=0, inplace=False)
        if transform_type == 'flip':
            transform1 = \
                transforms.Compose([transforms.RandomVerticalFlip(),
                                    transforms.RandomHorizontalFlip()])
        elif transform_type == 'rvf': #rotate, vertical flip
            transform1 = \
                transforms.Compose([MyRotation(angles=[0, 90, 180, 270]),
                                    transforms.RandomVerticalFlip()])
        elif transform_type in ['cbnfrsc', 'cbnfrs']:  # color, blur, noise, flip, rotate, scale, +-cutout
            transform1 = \
                transforms.Compose([
                    # transforms.ColorJitter(brightness=(0.65, 1.35), contrast=(0.5, 1.5),
                    transforms.ColorJitter(brightness=(0.85, 1.15), contrast=(0.75, 1.25),  # RanS 2.12.20
                                           saturation=0.1, hue=(-0.1, 0.1)),
                    transforms.GaussianBlur(3, sigma=(1e-7, 1e-1)), #RanS 23.12.20
                    MyGaussianNoiseTransform(sigma=(0, 0.05)),  #RanS 23.12.20
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.RandomAffine(degrees=0, scale=(1, 1 + scale_factor)), #RanS 18.4.21, avoid the need to keep larger patches
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                ])
        elif transform_type in ['pcbnfrsc', 'pcbnfrs']:  # parameterized color, blur, noise, flip, rotate, scale, +-cutout
            transform1 = \
                transforms.Compose([
                    # transforms.ColorJitter(brightness=(0.65, 1.35), contrast=(0.5, 1.5),
                    #transforms.ColorJitter(brightness=(1-c_param*1, 1+c_param*1), contrast=(1-c_param*2, 1+c_param*2),  # RanS 2.12.20
                    #                       saturation=c_param, hue=(-c_param, c_param)),
                    transforms.ColorJitter(brightness=color_param, contrast=color_param * 2, saturation=color_param, hue=color_param),
                    transforms.GaussianBlur(3, sigma=(1e-7, 1e-1)), #RanS 23.12.20
                    MyGaussianNoiseTransform(sigma=(0, 0.05)),  #RanS 23.12.20
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    #transforms.RandomAffine(degrees=0, scale=(1 - scale_factor, 1 + scale_factor)),
                    transforms.RandomAffine(degrees=0, scale=(1, 1 + scale_factor)), #RanS 18.4.21, avoid the need to keep larger patches
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                ])

        elif transform_type == 'aug_receptornet':  #
        #elif transform_type == 'c_0_05_bnfrsc' or 'c_0_05_bnfrs':  # color 0.1, blur, noise, flip, rotate, scale, +-cutout
            transform1 = \
                transforms.Compose([
                    transforms.ColorJitter(brightness=64.0/255, contrast=0.75, saturation=0.25, hue=0.04),
                    transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                    #Mean Pixel Regularization
                    transforms.ToTensor(),
                    Cutout(n_holes=1, length=100),  # RanS 24.12.20
                    MyMeanPixelRegularization(p=0.75)
                ])

        elif transform_type == 'cbnfr':  # color, blur, noise, flip, rotate
            transform1 = \
                transforms.Compose([
                    transforms.ColorJitter(brightness=(0.85, 1.15), contrast=(0.75, 1.25),  # RanS 2.12.20
                                           saturation=0.1, hue=(-0.1, 0.1)),
                    transforms.GaussianBlur(3, sigma=(1e-7, 1e-1)), #RanS 23.12.20
                    MyGaussianNoiseTransform(sigma=(0, 0.05)),  #RanS 23.12.20
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                ])
        elif transform_type in ['bnfrsc', 'bnfrs']:  # blur, noise, flip, rotate, scale, +-cutout
            transform1 = \
                transforms.Compose([
                    transforms.GaussianBlur(3, sigma=(1e-7, 1e-1)), #RanS 23.12.20
                    MyGaussianNoiseTransform(sigma=(0, 0.05)),  #RanS 23.12.20
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.RandomAffine(degrees=0, scale=(1, 1 + scale_factor)), #RanS 18.4.21, avoid the need to keep larger patches
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                ])
        elif transform_type == 'frs':  # flip, rotate, scale
            transform1 = \
                transforms.Compose([
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.RandomAffine(degrees=0, scale=(1, 1 + scale_factor)), #RanS 18.4.21, avoid the need to keep larger patches
                    transforms.CenterCrop(tile_size),  #fix boundary when scaling<1
                ])
        elif transform_type == 'hedcfrs':  # HED color, flip, rotate, scale
            transform1 = \
                transforms.Compose([
                    transforms.ColorJitter(brightness=(0.85, 1.15), contrast=(0.75, 1.25)),
                    HEDColorJitter(sigma=0.05),
                    transforms.RandomVerticalFlip(),
                    #transforms.RandomHorizontalFlip(),
                    MyRotation(angles=[0, 90, 180, 270]),
                    transforms.RandomAffine(degrees=0, scale=(1, 1 + scale_factor)), #RanS 18.4.21, avoid the need to keep larger patches
                    transforms.CenterCrop(tile_size),  # fix boundary when scaling<1
                    #transforms.functional.crop(top=0, left=0, height=tile_size, width=tile_size)
                    # fix boundary when scaling<1
                ])
        else:  # custom transform
            return transform_type

        transform = transforms.Compose([transform1, final_transform])
    else:
        transform = final_transform

    if transform_type in ['cbnfrsc', 'bnfrsc', 'c_0_05_bnfrsc', 'pcbnfrsc']:
        transform.transforms.append(Cutout(n_holes=1, length=100)) #RanS 24.12.20

    #RanS 14.1.21 - mean pixel regularization
    #if transform_type == 'aug_receptornet':
    #    transform.transforms.append(MyMeanPixelRegularization(p=0.75))
        #transform.transforms.append(transforms.RandomApply(torch.nn.ModuleList([MyMeanPixelRegularization]), p=0.75))

    return transform


def get_datasets_dir_dict(Dataset: str):
    dir_dict = {}
    TCGA_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/TCGA'
    ABCTB_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/ABCTB/ABCTB'
    HEROHE_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/HEROHE'
    SHEBA_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Sheba'
    ABCTB_TIF_gipdeep_path = r'/mnt/gipmed_new/Data/ABCTB_TIF'
    CARMEL_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Carmel'
    TCGA_LUNG_gipdeep_path = r'/mnt/gipmed_new/Data/Lung/TCGA_Lung/TCGA_LUNG'
    ALL_gipdeep_path = r'/mnt/gipmed_new/Data/BoneMarrow/ALL'
    AML_gipdeep_path = r'/mnt/gipmed_new/Data/BoneMarrow/AML/AML'
    Ipatimup_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Ipatimup'
    Covilha_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Covilha'
    TMA_HE_02_008_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/TMA/bliss_data/02-008/HE/TMA_HE_02-008'
    TMA_HE_01_011_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/TMA/bliss_data/01-011/HE/TMA_HE_01-011'
    HAEMEK_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Haemek'
    CARMEL_BENIGN_gipdeep_path = r'/mnt/gipmed_new/Data/Breast/Carmel/Benign'

    TCGA_ran_path = r'C:\ran_data\TCGA_example_slides\TCGA_examples_131020_flat\TCGA'
    HEROHE_ran_path = r'C:\ran_data\HEROHE_examples'
    ABCTB_ran_path = r'C:\ran_data\ABCTB\ABCTB_examples\ABCTB'
    TMA_HE_02_008_ran_path = r'C:\ran_data\TMA\02-008\TMA'

    TCGA_omer_path = r'/Users/wasserman/Developer/WSI_MIL/All Data/TCGA'
    HEROHE_omer_path = r'/Users/wasserman/Developer/WSI_MIL/All Data/HEROHE'
    CARMEL_omer_path = r'/Users/wasserman/Developer/WSI_MIL/All Data/CARMEL'

    if Dataset == 'ABCTB_TCGA':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['TCGA'] = TCGA_gipdeep_path
            #dir_dict['ABCTB'] = ABCTB_gipdeep_path
            dir_dict['ABCTB'] = ABCTB_TIF_gipdeep_path
        elif sys.platform == 'win32':  # GIPdeep
            dir_dict['TCGA'] = TCGA_ran_path
            dir_dict['ABCTB'] = ABCTB_ran_path

    elif Dataset == 'CARMEL':
        if sys.platform == 'linux':  # GIPdeep
            for ii in np.arange(1, 9):
                dir_dict['CARMEL' + str(ii)] = os.path.join(CARMEL_gipdeep_path, 'Batch_' + str(ii), 'CARMEL' + str(ii))
        elif sys.platform == 'darwin':  # Omer
            dir_dict['CARMEL'] = CARMEL_omer_path

    elif Dataset == 'CARMEL+BENIGN':
        if sys.platform == 'linux':  # GIPdeep
            for ii in np.arange(1, 9):
                dir_dict['CARMEL' + str(ii)] = os.path.join(CARMEL_gipdeep_path, 'Batch_' + str(ii), 'CARMEL' + str(ii))

            for ii in np.arange(1, 4):
                dir_dict['BENIGN' + str(ii)] = os.path.join(CARMEL_BENIGN_gipdeep_path, 'Batch_' + str(ii), 'BENIGN' + str(ii))

    elif Dataset == 'Carmel 9-11':
        if sys.platform == 'linux':  # GIPdeep
            for ii in np.arange(9, 12):
                dir_dict['CARMEL' + str(ii)] = os.path.join(CARMEL_gipdeep_path, 'Batch_' + str(ii), 'CARMEL' + str(ii))
        elif sys.platform == 'darwin':  # Omer
            raise Exception('Need to implement')

    elif (Dataset[:6] == 'CARMEL') and (len(Dataset) > 6):
        batch_num = Dataset[6:]
        if sys.platform == 'linux':  # GIPdeep
            dir_dict[Dataset] = os.path.join(CARMEL_gipdeep_path, 'Batch_' + batch_num, 'CARMEL' + batch_num)
        elif sys.platform == 'win32':  # Ran local
            dir_dict[Dataset] = TCGA_ran_path #temp for debug only

    elif (Dataset[:6] == 'BENIGN') and (len(Dataset) > 6):
        batch_num = Dataset[6:]
        if sys.platform == 'linux':  # GIPdeep
            dir_dict[Dataset] = os.path.join(CARMEL_BENIGN_gipdeep_path, 'Batch_' + batch_num, 'BENIGN' + batch_num)

    elif Dataset == 'CAT':
        if sys.platform == 'linux':  # GIPdeep
            for ii in np.arange(1, 9):
                dir_dict['CARMEL' + str(ii)] = os.path.join(CARMEL_gipdeep_path, 'Batch_' + str(ii), 'CARMEL' + str(ii))
            dir_dict['TCGA'] = TCGA_gipdeep_path
            #dir_dict['HEROHE'] = HEROHE_gipdeep_path
            #dir_dict['ABCTB'] = ABCTB_gipdeep_path
            dir_dict['ABCTB'] = ABCTB_TIF_gipdeep_path

        elif sys.platform == 'win32':  #Ran local
            dir_dict['TCGA'] = TCGA_ran_path
            dir_dict['HEROHE'] = HEROHE_ran_path

        elif sys.platform == 'darwin':   #Omer local
            dir_dict['TCGA'] = TCGA_omer_path
            dir_dict['CARMEL'] = CARMEL_omer_path

        else:
            raise Exception('Unrecognized platform')

    elif Dataset == 'TCGA_LUNG':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['TCGA_LUNG'] = TCGA_LUNG_gipdeep_path

    elif Dataset == 'TCGA':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['TCGA'] = TCGA_gipdeep_path

        elif sys.platform == 'win32':  # Ran local
            dir_dict['TCGA'] = TCGA_ran_path

        elif sys.platform == 'darwin':  # Omer local
            dir_dict['TCGA'] = TCGA_omer_path

        else:
            raise Exception('Unrecognized platform')

    elif Dataset == 'HEROHE':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['HEROHE'] = HEROHE_gipdeep_path

        elif sys.platform == 'win32':  # Ran local
            dir_dict['HEROHE'] = HEROHE_ran_path

        elif sys.platform == 'darwin':  # Omer local
            dir_dict['HEROHE'] = HEROHE_omer_path

    elif Dataset == 'ABCTB_TIF':
        if sys.platform == 'linux':  # GIPdeep
            #dir_dict['ABCTB_TIF'] = r'/home/womer/project/All Data/ABCTB_TIF'
            dir_dict['ABCTB_TIF'] = ABCTB_TIF_gipdeep_path
        elif sys.platform == 'darwin':  # Omer local
            dir_dict['ABCTB_TIF'] = r'All Data/ABCTB_TIF'
        else:
            raise Exception('Unsupported platform')

    elif Dataset == 'ABCTB_TILES':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['ABCTB_TILES'] = r'/home/womer/project/All Data/ABCTB_TILES'
        elif sys.platform == 'darwin':  # Omer local
            dir_dict['ABCTB_TILES'] = r'All Data/ABCTB_TILES'

    elif Dataset == 'ABCTB':
        if sys.platform == 'linux':  # GIPdeep Run from local files
            #dir_dict['ABCTB'] = ABCTB_gipdeep_path
            dir_dict['ABCTB'] = ABCTB_TIF_gipdeep_path
            #dir_dict['ABCTB'] = r'/mnt/gipmed_new/Data/Breast/ABCTB/mrxs_50test_temp/ABCTB' #temp RanS 9.11.21
            #dir_dict['ABCTB'] = r'/mnt/gipmed_new/Data/Breast/ABCTB/tif_49_slides' #temp RanS 9.11.21
            #dir_dict['ABCTB'] = r'/mnt/gipmed_new/Data/Breast/ABCTB/mrxs_50test_temp/duplicated'  # temp RanS 29.11.21
            #dir_dict['ABCTB'] = r'/mnt/gipmed_new/Data/Breast/ABCTB/tif_49_slides_duplicated' #temp RanS 9.11.21

        elif sys.platform == 'win32':  # Ran local
            dir_dict['ABCTB'] = ABCTB_ran_path

        elif sys.platform == 'darwin':  # Omer local
            dir_dict['ABCTB'] = r'All Data/ABCTB_TIF'

    elif Dataset == 'SHEBA':
        if sys.platform == 'linux':
            for ii in np.arange(2, 7):
                dir_dict['SHEBA' + str(ii)] = os.path.join(SHEBA_gipdeep_path, 'Batch_' + str(ii), 'SHEBA' + str(ii))
        elif sys.platform == 'win32':  # Ran local
            dir_dict['SHEBA5'] = r'C:\ran_data\Sheba\SHEBA5'

    elif Dataset == 'PORTO_HE':
        if sys.platform == 'linux':
            dir_dict['PORTO_HE'] = r'/mnt/gipmed_new/Data/Lung/PORTO_HE'
        elif sys.platform == 'win32':  # Ran local
            dir_dict['PORTO_HE'] = r'C:\ran_data\Lung_examples\LUNG'
        elif sys.platform == 'darwin':  # Omer local
            dir_dict['PORTO_HE'] = 'All Data/LUNG'

    elif Dataset == 'PORTO_PDL1':
        if sys.platform == 'linux':
            dir_dict['PORTO_PDL1'] = r'/mnt/gipmed_new/Data/Lung/sgils/LUNG/PORTO_PDL1'
        elif sys.platform == 'win32':  # Ran local
            #dir_dict['PORTO_PDL1'] = r'C:\ran_data\IHC_examples\PORTO_PDL1'
            dir_dict['PORTO_PDL1'] = r'C:\ran_data\IHC_examples\temp_8_slides\PORTO_PDL1'

    elif Dataset == 'LEUKEMIA':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['ALL'] = ALL_gipdeep_path
            dir_dict['AML'] = AML_gipdeep_path

    elif Dataset == 'AML':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['AML'] = ALL_gipdeep_path

    elif Dataset == 'ALL':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['ALL'] = ALL_gipdeep_path

    elif Dataset == 'IC':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['Ipatimup'] = Ipatimup_gipdeep_path
            dir_dict['Covilha'] = Covilha_gipdeep_path

    elif Dataset == 'HIC':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['Ipatimup'] = Ipatimup_gipdeep_path
            dir_dict['Covilha'] = Covilha_gipdeep_path
            dir_dict['HEROHE'] = HEROHE_gipdeep_path

    elif Dataset == 'TMA_HE_02_008':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['TMA_HE_02_008'] = TMA_HE_02_008_gipdeep_path
        else:
            dir_dict['TMA_HE_02_008'] = TMA_HE_02_008_ran_path

    elif Dataset == 'TMA_HE_01_011':
        if sys.platform == 'linux':  # GIPdeep
            dir_dict['TMA_HE_01_011'] = TMA_HE_01_011_gipdeep_path

    elif Dataset == 'HAEMEK':
        if sys.platform == 'linux':  # GIPdeep
            for ii in np.arange(1, 2):
                dir_dict['HAEMEK' + str(ii)] = os.path.join(HAEMEK_gipdeep_path, 'Batch_' + str(ii), 'HAEMEK' + str(ii))
            #dir_dict['HAEMEK'] = HAEMEK_gipdeep_path

    return dir_dict


def assert_dataset_target(DataSet, target_kind):
    #Support multi targets
    if type(target_kind) != list:
        target_kind = [target_kind]
    target_kind = set(target_kind)

    if DataSet == 'TMA_HE_02_008' and not target_kind <= {'ER', 'temp', 'binary_dist', 'binary_live', 'binary_any'}:
        raise ValueError('For TMA_HE_02_008 DataSet, target should be one of: ER, binary_dist, binary_live, binary_any')
    if DataSet == 'TMA_HE_01_011' and not target_kind <= {'binary_live', 'ER'}:
        raise ValueError('For TMA_HE_01_011 DataSet, target should be one of: binary_live, ER')
    elif DataSet == 'PORTO_HE' and not target_kind <= {'PDL1', 'EGFR', 'is_full_cancer'}:
        raise ValueError('For PORTO_HE DataSet, target should be one of: PDL1, EGFR')
    elif DataSet == 'PORTO_PDL1' and not target_kind <= {'PDL1'}:
        raise ValueError('For PORTO_PDL1 DataSet, target should be PDL1')
    elif (DataSet in ['TCGA', 'CAT', 'ABCTB_TCGA']) and not target_kind <= {'ER', 'PR', 'Her2', 'OR', 'is_cancer', 'Ki67'}:
        raise ValueError('target should be one of: ER, PR, Her2, OR, is_cancer, Ki67')
    elif (DataSet in ['IC', 'HIC', 'HEROHE', 'HAEMEK']) and not target_kind <= {'ER', 'PR', 'Her2', 'OR', 'Ki67'}:
        raise ValueError('target should be one of: ER, PR, Her2, OR')
    elif (DataSet == 'CARMEL') and not target_kind <= {'ER', 'PR', 'Her2', 'OR', 'Ki67', 'ER100'}:
        raise ValueError('target should be one of: ER, PR, Her2, OR')
    elif (DataSet == 'RedSquares') and not target_kind <= {'RedSquares'}:
        raise ValueError('target should be: RedSquares')
    elif DataSet == 'SHEBA' and not target_kind <= {'Onco', 'onco_score_11', 'onco_score_18', 'onco_score_26', 'onco_score_31', 'onco_score_all'}:
        raise ValueError('Invalid target for SHEBA DataSet')
    elif DataSet == 'TCGA_LUNG' and not target_kind <= {'is_cancer', 'is_LUAD', 'is_full_cancer'}:
        raise ValueError('for TCGA_LUNG DataSet, target should be is_cancer or is_LUAD')
    elif (DataSet in 'LEUKEMIA', 'ALL', 'AML') and not target_kind <= {'ALL','is_B','is_HR', 'is_over_6', 'is_over_10', 'is_over_15', 'WBC_over_20', 'WBC_over_50', 'is_HR_B', 'is_tel_aml_B', 'is_tel_aml_non_hr_B', 'MRD', 'AML'}:
        raise ValueError('for LEUKEMIA DataSets, target should be ALL, is_B, is_HR, is_over_6, is_over_10, is_over_15, WBC_over_20, WBC_over_50, is_HR_B, is_tel_aml_B, is_tel_aml_non_hr_B, MRD')
    elif (DataSet in ['ABCTB', 'ABCTB_TIF']) and not target_kind <= {'ER', 'PR', 'Her2', 'survival', 'Survival_Time', 'Survival_Binary'}:
        #raise ValueError('target should be one of: ER, PR, Her2, survival, Survival_Time, Survival_Binary')
        raise ValueError('target should be one of: ER, PR, Her2, survival, Survival_Time, Survival_Binary. {} was given as target'.format(target_kind))
    elif (DataSet == 'CARMEL+BENIGN') and not target_kind <= {'is_cancer'}:
        raise ValueError('target should be is_cancer')


def show_patches_and_transformations(X, images, tiles, scale_factor, tile_size):
    fig1, fig2, fig3, fig4, fig5 = plt.figure(), plt.figure(), plt.figure(), plt.figure(), plt.figure()
    fig1.set_size_inches(32, 18)
    fig2.set_size_inches(32, 18)
    fig3.set_size_inches(32, 18)
    fig4.set_size_inches(32, 18)
    fig5.set_size_inches(32, 18)
    grid1 = ImageGrid(fig1, 111, nrows_ncols=(2, 5), axes_pad=0)
    grid2 = ImageGrid(fig2, 111, nrows_ncols=(2, 5), axes_pad=0)
    grid3 = ImageGrid(fig3, 111, nrows_ncols=(2, 5), axes_pad=0)
    grid4 = ImageGrid(fig4, 111, nrows_ncols=(2, 5), axes_pad=0)
    grid5 = ImageGrid(fig5, 111, nrows_ncols=(2, 5), axes_pad=0)

    for ii in range(10):
        img1 = np.squeeze(images[ii, :, :, :])
        grid1[ii].imshow(np.transpose(img1, axes=(1, 2, 0)))

        img2 = np.squeeze(X[ii, :, :, :])
        grid2[ii].imshow(np.transpose(img2, axes=(1, 2, 0)))

        trans_no_norm = \
            transforms.Compose([
                transforms.ColorJitter(brightness=(0.85, 1.15), contrast=(0.75, 1.25), saturation=0.1,
                                       hue=(-0.1, 0.1)),
                transforms.RandomVerticalFlip(),
                transforms.RandomHorizontalFlip(),
                MyRotation(angles=[0, 90, 180, 270]),
                transforms.RandomAffine(degrees=0, scale=(1 - scale_factor, 1 + scale_factor)),
                transforms.CenterCrop(tile_size),  # fix boundary when scaling<1
                transforms.ToTensor()
            ])

        img3 = trans_no_norm(tiles[ii])
        grid3[ii].imshow(np.transpose(img3, axes=(1, 2, 0)))

        trans0 = transforms.ToTensor()
        img4 = trans0(tiles[ii])
        grid4[ii].imshow(np.transpose(img4, axes=(1, 2, 0)))

        color_trans = transforms.Compose([
            transforms.ColorJitter(brightness=(0.85, 1.15), contrast=(0.75, 1.25),  # RanS 2.12.20
                                   saturation=0.1, hue=(-0.1, 0.1)),
            #transforms.ColorJitter(brightness=64.0/255, contrast=0.75, saturation=0.25, hue=0.04),  # RanS 13.1.21
            transforms.ToTensor()])

        '''blur_trans = transforms.Compose([
            transforms.GaussianBlur(5, sigma=0.1),  # RanS 23.12.20
            transforms.ToTensor()])

        noise_trans = transforms.Compose([
            MyGaussianNoiseTransform(sigma=(0.05, 0.05)),  # RanS 23.12.20
            transforms.ToTensor()])

        cutout_trans = transforms.Compose([
            transforms.ToTensor(),
            Cutout(n_holes=1, length=100)])  # RanS 24.12.20]'''

        img5 = color_trans(tiles[ii])
        # img5 = blur_trans(tiles[ii])
        # img5 = noise_trans(tiles[ii])
        #img5 = cutout_trans(tiles[ii])
        grid5[ii].imshow(np.transpose(img5, axes=(1, 2, 0)))

    fig1.suptitle('original patches', fontsize=14)
    fig2.suptitle('final patches', fontsize=14)
    fig3.suptitle('all trans before norm', fontsize=14)
    fig4.suptitle('original patches, before crop', fontsize=14)
    fig5.suptitle('color transform only', fontsize=14)

    plt.show()


def get_model(model_name, saved_model_path='none'):
    #if train_type == 'MIL':
    # MIL models
    if model_name == 'resnet50_gn':
        model = ResNet50_GN_GatedAttention()
    elif model_name == 'receptornet':
        model = ReceptorNet('resnet50_2FC', saved_model_path)
    elif model_name == 'receptornet_preact_resnet50':
        model = ReceptorNet('preact_resnet50', saved_model_path)
    #elif train_type == 'REG':

    #REG models
    elif model_name == 'resnet50_3FC':
        model = nets.resnet50_with_3FC()
    elif model_name == 'preact_resnet50':
        model = nets.PreActResNet50()
    elif model_name == 'resnet50_gn':
        model = nets.ResNet50_GN()
    elif model_name == 'resnet18':
        model = nets.ResNet_18()
    elif model_name == 'resnet50':
        model = nets.ResNet_50()
    else:
        print('model not defined!')
    return model


def save_code_files(args: argsNamespace, train_DataSet):
    """
    This function saves the code files and argparse data to a Code directory within the run path.
    :param args: argsparse Namespace of the run.
    :return:
    """
    code_files_path = os.path.join(args.output_dir, 'Code')
    # Get the filename that called this function
    frame = inspect.stack()[1]
    module = inspect.getmodule(frame[0])
    full_filename = module.__file__
    args.run_file = full_filename.split('/')[-1]

    args_dict = vars(args)

    # Add Grid Data:
    data_dict = args_dict
    if train_DataSet is not None:
        if type(train_DataSet) == dict:
            train_DataSet = train_DataSet['Censored']

        if train_DataSet.train_type != 'Features':
            for _, key in enumerate(train_DataSet.dir_dict):
                grid_meta_data_file = os.path.join(train_DataSet.dir_dict[key], 'Grids_' + str(train_DataSet.desired_magnification), 'production_meta_data.xlsx')
                if os.path.isfile(grid_meta_data_file):
                    grid_data_DF = pd.read_excel(grid_meta_data_file)
                    grid_dict = grid_data_DF.to_dict('split')
                    grid_dict['dataset'] = key
                    grid_dict.pop('index')
                    grid_dict.pop('columns')
                    data_dict[key + '_grid'] = grid_dict

    data_DF = pd.DataFrame([data_dict]).transpose()

    if not os.path.isdir(code_files_path):
        #os.mkdir(args.output_dir)
        os.mkdir(code_files_path)
    data_DF.to_excel(os.path.join(code_files_path, 'run_arguments.xlsx'))
    # Get all .py files in the code path:
    py_files = glob.glob('*.py')
    for _, file in enumerate(py_files):
        #copy2(file, os.path.join(code_files_path, os.path.basename(file)))
        copyfile(file, os.path.join(code_files_path, os.path.basename(file)))

def extract_tile_scores_for_slide(all_features, models):
    """
    If all_features has shape[0] == 1024, than it;s originated from train type Receptor + is_Tumor.
    In that case we'll need only the first 512 features to compute the tile scores.
    """
    if all_features.shape[0] == 1024:
        all_features = all_features[:512, :]

    # Save tile scores and last models layer bias difference to file:
    tile_scores_list = []
    for index in range(len(models)):
        model = models[index]

        # Compute for each tile the multiplication between it's feature vector and the last layer weight difference vector:
        try:  # In case this part in not packed in Sequential we'll need this try statement
            last_layer_weights = model.classifier[0].weight.detach().cpu().numpy()
        except TypeError:
            last_layer_weights = model.classifier.weight.detach().cpu().numpy()

        f = last_layer_weights[1] - last_layer_weights[0]
        mult = np.matmul(f, all_features.detach().cpu())

        if len(mult.shape) == 1:
            tile_scores_list.append(mult)
        else:
            tile_scores_list.append(mult[:, index])

    return tile_scores_list


'''
def extract_tile_scores_for_slide_1(all_features, models, Output_Dirs, Epochs, data_path, slide_name):
    # Save tile scores and last models layer bias difference to file:
    tile_scores_list = []
    for index in range(len(models)):
        model = models[index]
        output_dir = Output_Dirs[index]
        epoch = Epochs[index]

        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference')):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference'))
        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores')):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores'))
        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch))):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch)))

        slide_score_filename = '.'.join(slide_name[0].split('/')[-1].split('.')[:-1]) + '--epoch_' + str(
            epoch) + '--scores.xlsx'
        full_slide_scores_filename = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), slide_score_filename)

        # Compute for each tile the multiplication between it's feature vector and the last layer weight difference vector:
        last_layer_weights = model.classifier[0].weight.detach().cpu().numpy()
        f = last_layer_weights[1] - last_layer_weights[0]
        mult = np.matmul(f, all_features)

        tile_scores_list.append(mult[:, index])

        model_bias_filename = 'epoch_' + str(epoch) + '-bias.xlsx'
        full_model_bias_filename = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch),
                                                model_bias_filename)

        if not os.path.isfile(full_model_bias_filename):
            last_layer_bias = model.classifier[0].bias.detach().cpu().numpy()
            last_layer_bias_diff = last_layer_bias[1] - last_layer_bias[0]

            last_layer_bias_DF = pd.DataFrame([last_layer_bias_diff])
            last_layer_bias_DF.to_excel(full_model_bias_filename)

    return tile_scores_list
'''

def save_all_slides_and_models_data(all_slides_tile_scores, all_slides_final_scores,
                                    all_slides_weights_before_softmax, all_slides_weights_after_softmax,
                                    models, Output_Dirs, Epochs, data_path, true_test_path: str = ''):

    # Save slide scores to file:
    for num_model in range(len(models)):
        if type(Output_Dirs) == str:
            output_dir = Output_Dirs
        elif type(Output_Dirs) is list:
            output_dir = Output_Dirs[num_model]

        epoch = Epochs[num_model]
        model = models[num_model]

        full_output_dir = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path)

        if not os.path.isdir(full_output_dir):
            Path(full_output_dir).mkdir(parents=True)

        '''if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference')):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference'))
        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores')):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores'))
        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch))):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch)))
        if true_test_path != '':
            if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path)):
                os.mkdir(os.path.join(full_output_dir, true_test_path))'''


        #model_bias_filename = 'epoch_' + str(epoch) + '-bias.xlsx'
        model_bias_filename = 'bias.xlsx'
        full_model_bias_filename = os.path.join(full_output_dir, model_bias_filename)
        if not os.path.isfile(full_model_bias_filename):
            try:  # In case this part in not packed in Sequential we'll need this try statement
                last_layer_bias = model.classifier[0].bias.detach().cpu().numpy()
            except TypeError:
                last_layer_bias = model.classifier.bias.detach().cpu().numpy()

            last_layer_bias_diff = last_layer_bias[1] - last_layer_bias[0]

            last_layer_bias_DF = pd.DataFrame([last_layer_bias_diff])
            last_layer_bias_DF.to_excel(full_model_bias_filename)

        if type(all_slides_tile_scores) == dict:
            all_slides_tile_scores_REG = all_slides_tile_scores['REG']
            all_slides_final_scores_REG = all_slides_final_scores['REG']
            all_slides_tile_scores = all_slides_tile_scores['MIL']
            all_slides_final_scores = all_slides_final_scores['MIL']

            all_slides_tile_scores_REG_DF = pd.DataFrame(all_slides_tile_scores_REG[num_model]).transpose()
            all_slides_final_scores_REG_DF = pd.DataFrame(all_slides_final_scores_REG[num_model]).transpose()

            tile_scores_file_name_REG = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'tile_scores_REG.xlsx')
            slide_score_file_name_REG = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'slide_scores_REG.xlsx')

            all_slides_tile_scores_REG_DF.to_excel(tile_scores_file_name_REG)
            all_slides_final_scores_REG_DF.to_excel(slide_score_file_name_REG)

        all_slides_tile_scores_DF = pd.DataFrame(all_slides_tile_scores[num_model]).transpose()
        all_slides_final_scores_DF = pd.DataFrame(all_slides_final_scores[num_model]).transpose()
        all_slides_weights_before_sofrmax_DF = pd.DataFrame(all_slides_weights_before_softmax[num_model]).transpose()
        all_slides_weights_after_softmax_DF = pd.DataFrame(all_slides_weights_after_softmax[num_model]).transpose()

        tile_scores_file_name = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'tile_scores.xlsx')
        slide_score_file_name = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'slide_scores.xlsx')
        tile_weights_before_softmax_file_name = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'tile_weights_before_softmax.xlsx')
        tile_weights_after_softmax_file_name = os.path.join(data_path, output_dir, 'Inference', 'Tile_Scores', 'Epoch_' + str(epoch), true_test_path, 'tile_weights_after_softmax.xlsx')

        all_slides_tile_scores_DF.to_excel(tile_scores_file_name)
        all_slides_final_scores_DF.to_excel(slide_score_file_name)
        all_slides_weights_before_sofrmax_DF.to_excel(tile_weights_before_softmax_file_name)
        all_slides_weights_after_softmax_DF.to_excel(tile_weights_after_softmax_file_name)

        print('Tile scores for model {}/{} has been saved !'.format(num_model + 1, len(models)))


def map_original_grid_list_to_equiv_grid_list(adjusted_tile_size, grid_list):
    """
    This function is used in datasets.Full_Slide_Inference_Dataset.
    It's use is to find the corresponding locations in the equivalent grid list of the tiles in the original_grid_list
    """
    equivalent_grid = []
    for location in grid_list:
        equivalent_location = (location[0] // adjusted_tile_size, location[1] // adjusted_tile_size)
        equivalent_grid.append(equivalent_location)

    return equivalent_grid

def gather_per_patient_data(all_targets, all_scores_for_class_1, all_patient_barcodes):
    """
    This function gets 3 lists containing data about slides targets, scores (for class 1 - positive) and patient barcodes.
    The function computes and returns the mean score for all slides that belong to the same patient.
    The function uses the targets list to make sure that all targets for the same patient are the equal and return it's value

    :param all_targets:
    :param all_scores_for_class_1:
    :param all_patient_barcodes:
    :return:
    """

    targets_dict = {}
    scores_dict = {}

    # first, we'll gather all the data for specific patients
    for idx, patient in enumerate(all_patient_barcodes):
        if patient in targets_dict:
            targets_dict[patient].append(all_targets[idx])
            scores_dict[patient].append(all_scores_for_class_1[idx])
        else:
            targets_dict[patient] = [all_targets[idx]]
            scores_dict[patient] = [all_scores_for_class_1[idx]]

    # Now, we'll find the mean values for each patient:
    all_targets_per_patient, all_scores_for_class_1_per_patient = [], []
    patient_barcodes = targets_dict.keys()
    for barcode in patient_barcodes:
        targets = np.array(targets_dict[barcode])
        scores_mean = np.array(scores_dict[barcode]).mean()

        # Check that all targets for the same patient are the same.
        if targets[0] != targets.mean():
            raise Exception('Not all targets for patient {} are equal'.format(barcode))

        all_targets_per_patient.append(int(targets.mean()))
        all_scores_for_class_1_per_patient.append(scores_mean)

    return all_targets_per_patient, all_scores_for_class_1_per_patient


def balance_dataset(meta_data_DF, censor_balance: bool = False, test_fold=1):
    seed(2021)

    if censor_balance:
        # We need to balance the train set
        train_folds = set(meta_data_DF['test fold idx'])
        if 'test' in train_folds:
            train_folds.remove('test')
        if 'val' in train_folds:
            train_folds.remove('val')

        train_folds.remove(test_fold)

        # Now we'll remove all slides that do not belong to the train set:
        meta_data_DF = meta_data_DF[meta_data_DF['test fold idx'].isin(train_folds)]

        meta_data_DF['use_in_balanced_dataset'] = 0
        meta_data_DF.loc[meta_data_DF['Censored'] == 0, 'use_in_balanced_dataset'] = 1  # take all not censored

        not_censored_slides = meta_data_DF[meta_data_DF['Censored'] == 0]['file'].to_list()
        censored_slides = meta_data_DF[meta_data_DF['Censored'] == 1]['file'].to_list()

        # pick which censored slides to take:
        censored_patients_to_take = sample(censored_slides, k=len(not_censored_slides))
        print('Not censored slides: {}'.format(len(not_censored_slides)))
        print('Censored slides: {}'.format(len(censored_slides)))

        for patient_to_take in censored_patients_to_take:
            if len(meta_data_DF.loc[meta_data_DF['file'] == patient_to_take]) > 1:
                print(len(meta_data_DF.loc[meta_data_DF['file'] == patient_to_take]))
                print(patient_to_take)
                raise Exception('debug')

            meta_data_DF.loc[meta_data_DF['file'] == patient_to_take, 'use_in_balanced_dataset'] = 1

        meta_data_DF = meta_data_DF[meta_data_DF['use_in_balanced_dataset'] == 1]
        meta_data_DF.reset_index(inplace=True)

    else:
        meta_data_DF['use_in_balanced_data_ER'] = 0
        meta_data_DF.loc[meta_data_DF['ER status'] == 'Negative', 'use_in_balanced_data_ER'] = 1  # take all negatives

        # from all positives, take the same amount as negatives
        patient_list, patient_ind_list, patient_inverse_list = np.unique(
            np.array(meta_data_DF['patient barcode']).astype('str'), return_index=True, return_inverse=True)

        # get patient status for each patient
        # for patients with multiple statuses, the first one will be taken. These cases are rare.
        patient_status = []
        for i_patient in patient_ind_list:
            patient_status.append(meta_data_DF.loc[i_patient, 'ER status'])

        N_negative_patients = np.sum(np.array(patient_status) == 'Negative')
        positive_patient_ind_list = np.where(np.array(patient_status) == 'Positive')

        # take N_negative_patients positive patient
        positive_patients_inds_to_take = sample(list(positive_patient_ind_list[0]), k=N_negative_patients)
        for patient_to_take in positive_patients_inds_to_take:
            meta_data_DF.loc[patient_inverse_list == patient_to_take, 'use_in_balanced_data_ER'] = 1

    return meta_data_DF


class FocalLoss(torch.nn.Module):
    def __init__(self, weight=None, gamma=2):
        super(FocalLoss, self).__init__()
        w = weight if weight is not None else torch.FloatTensor([1., 1.])
        self.register_buffer("weight", w)
        #self.weight = w #RanS 18.7.21
        self.gamma = gamma

    def forward(self, input, target):
        ce = F.cross_entropy(input, target.long(), reduction='none')
        pt = torch.exp(-ce)
        ce *= torch.matmul(torch.nn.functional.one_hot(target.long(), num_classes=2).float(), self.weight)
        return ((1 - pt) ** self.gamma * ce).mean()


class EmbedSquare(object):
    def __init__(self, size=16, stride=8, pad=4, minibatch_size=1, color='Testing'):
        self.size = size
        self.stride = stride
        self.pad = pad
        self.minibatch = minibatch_size
        self.normalized_square = torch.zeros(1, 3, self.size, self.size)
        self.color = color

        if color == 'Black':
            self.normalized_square[:, 0, :, :], \
            self.normalized_square[:, 1, :, :], \
            self.normalized_square[:, 2, :, :] = -7.9982, -4.7133, -11.8895  # Value of BLACK pixel after normalization
        elif color == 'White':
            self.normalized_square[:, 0, :, :], \
            self.normalized_square[:, 1, :, :], \
            self.normalized_square[:, 2, :, :] = 0.8907, 0.9977, 0.8170  # Value of WHITE pixel after normalization
        elif color == 'Gray':
            self.normalized_square[:, 0, :, :], \
            self.normalized_square[:, 1, :, :], \
            self.normalized_square[:, 2, :, :] = -3.5712, - 1.8690, - 5.5611  # Value of GRAY pixel after normalization
        elif color == 'Testing':
            self.normalized_square = torch.ones(1, 3, self.size, self.size) * 255
        else:
            raise Exception('Color choice do not match to any of the options (White/ Black)')

    def __call__(self, image):
        if len(image.shape) == 3:
            _, tile_size, _ = image.shape
        elif len(image.shape) == 4:
            _, _, tile_size, _ = image.shape

        if type(image) != torch.Tensor:
            raise Exception('Image is not in correct format')

        new_image = torch.zeros((1, 3, tile_size + 2 * self.pad, tile_size + 2 * self.pad))
        new_image[:, :, self.pad:self.pad + tile_size, self.pad:self.pad + tile_size] = image

        init = {'Row': 0,
                'Col': 0}
        '''init_2048 = {'Row': 0,
                      'Col': 0}'''

        total_jumps = 256 // self.stride
        output_images = []
        output_images.append(image.reshape([1, 3, tile_size, tile_size]))  # Adding the basic tile to the list
        minibatch_size = 0
        counter = 0
        #quit=False
        image_output_minibatch = torch.zeros((self.minibatch, 3, tile_size, tile_size))
        if tile_size == 2048:  # We'll need to put 16 squares (4X4) and not just one
            # At first we need to create a basic square mask.
            basic_mask = np.zeros((1808, 1808), dtype=bool)
            for row_idx in range(8):
                for col_idx in range(8):
                    basic_mask[256 * row_idx:256 * row_idx + 16, 256 * col_idx:256 * col_idx + 16] = True

        print('Creating square embeded tiles...')
        for row_idx in tqdm(range(0, total_jumps)):
            '''if quit:
                break'''
            init['Row'] = row_idx * self.stride
            init['Col'] = 0
            for col_idx in range(0, total_jumps):
                '''if quit:
                    break'''
                image_output = torch.clone(new_image)
                init['Col'] = col_idx * self.stride
                if tile_size == 256:
                    image_output[:, :, init['Row']:self.size + init['Row'], init['Col']:self.size + init['Col']] = self.normalized_square

                elif tile_size == 2048:  # We need to put 16 squares (4X4) and not just one
                    mask = np.zeros_like(image_output, dtype=bool)
                    mask[:, :, init['Row']:1808 + init['Row'], init['Col']:1808 + init['Col']] = basic_mask
                    np.place(image_output.numpy(), mask, self.normalized_square[0, 0, 0, 0].item())

                    '''for row_2048_idx in range(0, 8):
                        init_2048['Row'] = init['Row'] + row_2048_idx * 256  # 256 is the stride for multiple cutouts in a big tile
                        init_2048['Col'] = 0
                        #plt.imshow(image_output.squeeze(0).numpy().transpose(1, 2, 0))
                        for col_2048_idx in range(0, 8):
                            init_2048['Col'] = init['Col'] + col_2048_idx * 256  # 256 is the stride for multiple cutouts in a big tile
                            if init_2048['Row'] == init['Row'] and init_2048['Col'] == init['Col']:
                                continue  # We can skip the first square since it has already been inserted

                            image_output[:, :,
                            init_2048['Row']:self.size + init_2048['Row'],
                            init_2048['Col']:self.size + init_2048['Col']] = self.normalized_square'''


                # Now we have to cut the padding:
                image_output = image_output[:, :, self.pad: + self.pad + tile_size, self.pad: + self.pad + tile_size]

                # And add the output image to the list of ready images with the cutout:
                image_output_minibatch[minibatch_size, :, :, :] = image_output
                minibatch_size += 1
                counter += 1

                if counter == 1024:
                    output_images.append(image_output_minibatch[:minibatch_size, :, :, :])
                    break
                if minibatch_size == self.minibatch:
                    output_images.append(image_output_minibatch)
                    minibatch_size = 0
                    image_output_minibatch = torch.zeros((self.minibatch, 3, tile_size, tile_size))
                '''if len(output_images) == 6:
                    quit=True'''

        return output_images


def dataset_properties_to_location(dataset_name_list: list, receptor: str, test_fold: int, is_train: bool = False):
    # Basic data definition:
    if sys.platform == 'darwin':
        dataset_full_data_dict = {'TCGA_ABCTB':
                                      {'ER':
                                           {1:
                                                {'Train': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/Train',
                                                 'Test': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/Test',
                                                 'Dataset name': r'FEATURES: Exp_293-ER-TestFold_1'
                                                 }
                                            }
                                       },
                                  'CAT':
                                      {'ER':
                                           {1:
                                                {'Train': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Train',
                                                 'Test': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Test',
                                                 'Dataset name': r'FEATURES: Exp_355-ER-TestFold_1',
                                                 'Regular model location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CAT_355_TF_1/model_data_Epoch_1000.pt'}
                                            }
                                       },
                                  'CARMEL':
                                      {'ER':
                                           {1:
                                                {'Train': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_358-TestFold_1/Train',
                                                 'Test': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_358-TestFold_1/Test',
                                                 'Dataset name': r'FEATURES: Exp_358-ER-TestFold_1',
                                                 'Regular model location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CARMEL_358-TF_1/model_data_Epoch_1000.pt'
                                                 }
                                            }
                                       },
                                  'CARMEL_40':
                                      {'ER':
                                           {1:
                                                {
                                                    'Train': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_381-TestFold_1/Train',
                                                    'Test': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_381-TestFold_1/Test',
                                                    'Dataset name': r'FEATURES: Exp_381-ER-TestFold_1',
                                                    'Regular model location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CARMEL_381-TF_1/model_data_Epoch_1200.pt'
                                                    }
                                            }
                                       }
                                  }
    elif sys.platform == 'linux':
        dataset_full_data_dict = {'TCGA_ABCTB':
                                      {'ER':
                                           {1:
                                                {'Train': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/train_inference_w_features',
                                                 'Test': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/test_inference_w_features',
                                                 'Dataset name': r'FEATURES: Exp_293-ER-TestFold_1'
                                                 }
                                            }
                                       },
                                  'CAT':
                                      {'ER':
                                           {1:
                                                {'Train': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/train_w_features',
                                                 'Test': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/test_w_features',
                                                 'Dataset name': r'FEATURES: Exp_355-ER-TestFold_1',
                                                 'Regular model location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'}
                                            }
                                       },
                                  'CARMEL':
                                      {'ER':
                                           {1:
                                                {'Train': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Inference/train_w_features',
                                                 'Test': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Inference/test_w_features',
                                                 'Dataset name': r'FEATURES: Exp_358-ER-TestFold_1',
                                                 'Regular model location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                 }
                                            }
                                       },
                                  'CARMEL_40':
                                      {'ER':
                                          {1:
                                              {
                                                  'Train': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Inference/train_w_features',
                                                  'Test': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Inference/test_w_features',
                                                  'Dataset name': r'FEATURES: Exp_358-ER-TestFold_1',
                                                  'Regular model location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1200.pt'
                                              }
                                          }
                                      }
                                  }

    dataset_location_list = []

    if receptor == 'ER_Features':
        receptor = 'ER'
    for dataset in dataset_name_list:
        location = dataset_full_data_dict[dataset][receptor][test_fold]['Train' if is_train else 'Test']
        dataset_name = dataset_full_data_dict[dataset][receptor][test_fold]['Dataset name']
        regular_model_location = dataset_full_data_dict[dataset][receptor][test_fold]['Regular model location']
        dataset_location_list.append([dataset, location, dataset_name, regular_model_location])

    return dataset_location_list


def get_label(target, multi_target=False):
    if multi_target:
        label = []
        for t in target:
            label.append(get_label(t))
        return label
    else:
        if target == 'Positive':
            return [1]
        elif target == 'Negative':
            return [0]
        elif (isinstance(target, int) or isinstance(target, float)) and not np.isnan(target): #RanS 17.1.22, support multiclass
            return [int(target)]
        else: #unknown
            return [-1]

def num_2_bool(num):
    if num == 1:
        return True
    elif num == 0:
        return False
    else:
        return -1



def get_RegModel_Features_location_dict(train_DataSet: str, target: str, test_fold: int):
    All_Data_Dict = {'linux': {'CAT': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_355-ER-TestFold_1',
                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/train_w_features',
                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/test_w_features',
                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         },
                                                  'Her2': {'DataSet Name': r'FEATURES: Exp_392-Her2-TestFold_1',
                                                           'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Inference/train_w_features',
                                                           'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Inference/test_w_features',
                                                           'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                           },
                                                  'PR': {'DataSet Name': r'FEATURES: Exp_10-PR-TestFold_1',
                                                         'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Inference/train_w_features',
                                                         'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Inference/test_w_features',
                                                         'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         }
                                                  },
                                       'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Exp_393-ER-TestFold_2',
                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Inference/train_w_features',
                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Inference/test_w_features',
                                                         #'TestSet Location': r'/home/womer/project/All Data/Ran_Features/393/Test',  # This is the scrambled test set
                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         },
                                                  'PR': {'DataSet Name': r'FEATURES: Exp_20063-PR-TestFold_2',
                                                         'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Inference/train_w_features',
                                                         'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Inference/test_w_features',
                                                         'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         },
                                                  'Her2': {'DataSet Name': r'FEATURES: Exp_412-Her2-TestFold_2',
                                                           'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Inference/train_w_features',
                                                           'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Inference/test_w_features',
                                                           'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                           }
                                                  },
                                       'Fold 3': {'ER': {'DataSet Name': r'FEATURES: Exp_472-ER-TestFold_3',
                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_472-ER-TestFold_3/Inference/train_w_features',
                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_472-ER-TestFold_3/Inference/test_w_features',
                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_472-ER-TestFold_3/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         },
                                                  'Her2': {'DataSet Name': r'FEATURES: Exp_20114-Her2-TestFold_3',
                                                           'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20114-Her2-TestFold_3/Inference/train_w_features',
                                                           'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20114-Her2-TestFold_3/Inference/test_w_features',
                                                           'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20114-Her2-TestFold_3/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                           },
                                                  'PR': {'DataSet Name': r'FEATURES: Exp_497-PR-TestFold_3',
                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_497-PR-TestFold_3/Inference/train_w_features',
                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_497-PR-TestFold_3/Inference/test_w_features',
                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_497-PR-TestFold_3/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         }
                                                  },
                                       'Fold 4': {'ER': {'DataSet Name': r'FEATURES: Exp_542-ER-TestFold_4',
                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Inference/train_w_features',
                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Inference/test_w_features',
                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         },
                                                  'Her2': {'DataSet Name': r'FEATURES: Exp_20201-Her2-TestFold_4',
                                                           'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Inference/train_w_features',
                                                           'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Inference/test_w_features',
                                                           'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                           },
                                                  'PR': {'DataSet Name': r'FEATURES: Exp_20207-PR-TestFold_4',
                                                         'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20207-PR-TestFold_4/Inference/train_w_features',
                                                         'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20207-PR-TestFold_4/Inference/test_w_features',
                                                         'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20207-PR-TestFold_4/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                         }
                                                  },
                                       'Fold 5': {'Her2': {'DataSet Name': r'FEATURES: Exp_20228-Her2-TestFold_5',
                                                           'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Inference/train_w_features',
                                                           'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Inference/test_w_features',
                                                           'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                           }
                                                  }
                                       },
                               'CAT with Location': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_355-ER-TestFold_1 With Locations',
                                                                       'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/train_w_features_locs',
                                                                       'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/test_w_features_locs',
                                                                       'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                       },
                                                                'Her2': {'DataSet Name': r'FEATURES: Exp_392-Her2-TestFold_1 With Locations',
                                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Inference/train_w_features',
                                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Inference/test_w_locs_w_features',
                                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                         },
                                                                'is_Tumor_for_Her2': {'DataSet Name': r'FEATURES: Exp_627-is_Tumor_for_Her2-TestFold_1 With Locations',
                                                                                      'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_Her2_train_fold2345_patches_inference_w_features',
                                                                                      'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_Her2_fold1_patches_inference_w_features',
                                                                                      'REG Model Location': None
                                                                                      },
                                                                'PR': {'DataSet Name': r'FEATURES: Exp_10-PR-TestFold_1  With Locations',
                                                                       'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Inference/train_w_features_new',
                                                                       'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Inference/test_w_locs_w_features',
                                                                       'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                       },
                                                                'is_Tumor_for_PR': {'DataSet Name': r'FEATURES: Exp_627-is_Tumor_for_PR-TestFold_1 With Locations',
                                                                                    'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_PR_train_fold2345_patches_inference_w_features',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_PR_fold1_patches_inference_w_features',
                                                                                    'REG Model Location': None
                                                                                    },
                                                                'ER_for_is_Tumor': {'DataSet Name': r'FEATURES: Exp_355-ER-TestFold_1 With Locations for is_Tumor',
                                                                                    'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/train_w_features_w_locs',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/test_w_features_locs',
                                                                                    'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                                    },
                                                                'is_Tumor_for_ER': {'DataSet Name': r'FEATURES: Exp_627-is_Tumor_for_ER-TestFold_1 With Locations',
                                                                                    'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_ER_train_fold2345_patches_inference_w_features',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_627-is_cancer-TestFold_1/Inference/CAT_ER_test_fold1_patches_inference_w_features',
                                                                                    'REG Model Location': None
                                                                                    }
                                                                },

                                                     'Fold 2': {'ER_for_is_Tumor': {'DataSet Name': r'FEATURES: Exp_393-ER-TestFold_2 With Locations',
                                                                                    'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Inference/train_w_features_new',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Inference/test_w_features',
                                                                                    'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_393-ER-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                                    },
                                                                'Her2': {'DataSet Name': r'FEATURES: Exp_412-Her2-TestFold_2 With Locations',
                                                                         'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Inference/train_w_features',
                                                                         'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Inference/test_w_features',
                                                                         'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_412-Her2-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                         },
                                                                'PR': {'DataSet Name': r'FEATURES: Exp_20063-PR-TestFold_2 With Locations',
                                                                       'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Inference/train_w_features_new',
                                                                       'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Inference/test_w_features',
                                                                       'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20063-PR-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                       },
                                                                'is_Tumor_for_Her2': {'DataSet Name': r'FEATURES: Exp_641-is_Tumor_for_Her2-TestFold_2 With Locations',
                                                                                      'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_Her2_train_fold1345_patches_inference_w_features',
                                                                                      'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_Her2_test_fold2_patches_inference_w_features',
                                                                                      'REG Model Location': None
                                                                                      },
                                                                'is_Tumor_for_ER': {'DataSet Name': r'FEATURES: Exp_641-is_Tumor_for_ER-TestFold_2 With Locations',
                                                                                    'TrainSet Location': r'features:/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_ER_train_fold1345_patches_inference_w_features',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_ER_test_fold2_patches_inference_w_features',
                                                                                    'REG Model Location': None
                                                                                    },
                                                                'is_Tumor_for_PR': {'DataSet Name': r'FEATURES: Exp_641-is_Tumor_for_PR-TestFold_2 With Locations',
                                                                                    'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_PR_train_fold1345_patches_inference_w_features',
                                                                                    'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_641-is_cancer-TestFold_2/Inference/CAT_PR_test_fold2_patches_inference_w_features',
                                                                                    'REG Model Location': None
                                                                                    }
                                                                }
                                                     },
                               'CARMEL': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_358-ER-TestFold_1',
                                                            'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Inference/train_w_features',
                                                            'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Inference/test_w_features',
                                                            'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_358-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                            },
                                                     'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1',
                                                              'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/train_w_features',
                                                              'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/test_w_features',
                                                              'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                              },
                                                     'Her2': {'DataSet Name': None,
                                                              'TrainSet Location': None,
                                                              'TestSet Location': None,
                                                              'REG Model Location': None
                                                              },
                                                     'PR': {'DataSet Name': None,
                                                            'TrainSet Location': None,
                                                            'TestSet Location': None,
                                                            'REG Model Location': None
                                                            }
                                                     },
                                          'Fold 2': {'Ki67': {'DataSet Name': r'FEATURES: Exp_490-Ki67-TestFold_2',
                                                              'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_490-Ki67-TestFold_2/Inference/train_w_features',
                                                              'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_490-Ki67-TestFold_2/Inference/test_w_features',
                                                              'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_490-Ki67-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                              }
                                                     }
                                          },
                               'CARMEL 9-11': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_355-ER-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                 'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                 },
                                                          'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                   'TestSet Location': {'Carmel 9': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/CARMEL9/',
                                                                                        'Carmel 10': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/CARMEL10/',
                                                                                        'Carmel 11': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/CARMEL11/'
                                                                                        }
                                                                   },
                                                          'Her2': {'DataSet Name': None,
                                                                   'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                   },
                                                          'PR': {'DataSet Name': None,
                                                                 'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                 }
                                                          },
                                               'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_393-ER-TestFold_2, CARMEL ONLY Slides Batch 9-11',
                                                                 'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                 },
                                                          'Her2': {'DataSet Name': None,
                                                                   'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                   },
                                                          'PR': {'DataSet Name': None,
                                                                 'TestSet Location': {'Carmel 9': None,
                                                                                      'Carmel 10': None,
                                                                                      'Carmel 11': None
                                                                                      }
                                                                 }
                                                          },
                                               'Fold 4': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_542-ER-TestFold_4, CARMEL ONLY Slides Batch 9-11',
                                                                 'TestSet Location': {'Carmel 9': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Inference/CARMEL9',
                                                                                      'Carmel 10': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Inference/CARMEL10',
                                                                                      'Carmel 11': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_542-ER-TestFold_4/Inference/CARMEL11'
                                                                                      }
                                                                 },
                                                          'Her2': {'DataSet Name': r'FEATURES: Model From Exp_20201-Her2-TestFold_4, CARMEL ONLY Slides Batch 9-11',
                                                                   'TestSet Location': {'Carmel 9': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Inference/CARMEL9',
                                                                                        'Carmel 10': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Inference/CARMEL10',
                                                                                        'Carmel 11': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20201-Her2-TestFold_4/Inference/CARMEL11'
                                                                                        }
                                                                   }
                                                          },
                                               'Fold 5': {'Her2': {'DataSet Name': r'FEATURES: Model From Exp_20228-Her2-TestFold_5, CARMEL ONLY Slides Batch 9-11',
                                                                   'TestSet Location': {'Carmel 9': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Inference/CARMEL9',
                                                                                        'Carmel 10': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Inference/CARMEL10',
                                                                                        'Carmel 11': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20228-Her2-TestFold_5/Inference/CARMEL11'
                                                                                        }
                                                                   }
                                                          }
                                               },
                               'HAEMEK': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_355-ER-TestFold_1, HAEMEK ONLY Slides',
                                                            'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_355-ER-TestFold_1/Inference/HAEMEK'
                                                            },
                                                     'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1, HAEMEK ONLY Slides',
                                                              'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_419-Ki67-TestFold_1/Inference/HAEMEK'
                                                              },
                                                     'Her2': {'DataSet Name': r'FEATURES: Model From Exp_392-Her2-TestFold_1, HAEMEK ONLY Slides',
                                                              'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_392-Her2-TestFold_1/Inference/HAEMEK'
                                                              },
                                                     'PR': {'DataSet Name': r'FEATURES: Model From Exp_20010-PR-TestFold_1, HAEMEK ONLY Slides',
                                                            'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_10-PR-TestFold_1/Inference/HAEMEK'
                                                            }
                                                     }
                                          },
                               'ABCTB': {'Fold 1': {'survival': {'DataSet Name': r'FEATURES: Exp_20094-survival-TestFold_1',
                                                                 'TrainSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20094-survival-TestFold_1/Inference/train_w_features/',
                                                                 'TestSet Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20094-survival-TestFold_1/Inference/test_w_features/',
                                                                 'REG Model Location': r'/mnt/gipnetapp_public/sgils/ran/runs/Exp_20094-survival-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                 }
                                                    }
                                         },
                               'TCGA_ABCTB->CARMEL': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_293-ER-TestFold_1',
                                                                             'TestSet Location': {'CARMEL': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/CARMEL1-8',
                                                                                                  'CARMEL 9-11': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/CARMEL9-11'
                                                                                                  }
                                                                             },
                                                                      'Her2': {'DataSet Name': r'FEATURES: Exp_308-Her2-TestFold_1',
                                                                               'TestSet Location': {'CARMEL': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_308-Her2-TestFold_1/Inference/CARMEL1-8',
                                                                                                    'CARMEL 9-11': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_308-Her2-TestFold_1/Inference/CARMEL9-11'
                                                                                                    }
                                                                               },
                                                                      'PR': {'DataSet Name': r'FEATURES: Exp_309-PR-TestFold_1',
                                                                             'TestSet Location': {'CARMEL': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_309-PR-TestFold_1/Inference/CARMEL1-8',
                                                                                                  'CARMEL 9-11': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_309-PR-TestFold_1/Inference/CARMEL9-11'
                                                                                                  }
                                                                             }
                                                                      }
                                                           },
                               'TCGA_ABCTB': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_293-ER-TestFold_1',
                                                                'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/train_inference_w_features',
                                                                'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Inference/test_inference_w_features',
                                                                'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_293-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                },
                                                         'Her2': {'DataSet Name': r'FEATURES: Exp_308-Her2-TestFold_1',
                                                                  'TrainSet Location': r'/home/womer/project/All Data/Ran_Features/Her2/Fold_1/Train',
                                                                  'TestSet Location': r'/home/womer/project/All Data/Ran_Features/Her2/Fold_1/Test',
                                                                  'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_308-Her2-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                  },
                                                         'PR': {'DataSet Name': r'FEATURES: Exp_309-PR-TestFold_1',
                                                                'TrainSet Location': r'/home/womer/project/All Data/Ran_Features/PR/Fold_1/Train',
                                                                'TestSet Location': r'/home/womer/project/All Data/Ran_Features/PR/Fold_1/Test',
                                                                'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_309-PR-TestFold_1/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                }
                                                         },
                                              'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Exp_299-ER-TestFold_2',
                                                                'TrainSet Location': r'/home/womer/project/All Data/Ran_Features/299/Train',
                                                                'TestSet Location': r'/home/womer/project/All Data/Ran_Features/299/Test',
                                                                'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_299-ER-TestFold_2/Model_CheckPoints/model_data_Epoch_1000.pt'
                                                                },
                                                         'Her2': {'DataSet Name': None,
                                                                  'TrainSet Location': None,
                                                                  'TestSet Location': None,
                                                                  'REG Model Location': None
                                                                  },
                                                         'PR': {'DataSet Name': None,
                                                                'TrainSet Location': None,
                                                                'TestSet Location': None,
                                                                'REG Model Location': None
                                                                }
                                                         }
                                              },
                               'CARMEL_40': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_381-ER-TestFold_1',
                                                               'TrainSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Inference/train_w_features',
                                                               'TestSet Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Inference/test_w_features',
                                                               'REG Model Location': r'/home/rschley/code/WSI_MIL/general_try4/runs/Exp_381-ER-TestFold_1/Model_CheckPoints/model_data_Epoch_1200.pt'
                                                               },
                                                        'Her2': {'DataSet Name': None,
                                                                 'TrainSet Location': None,
                                                                 'TestSet Location': None,
                                                                 'REG Model Location': None
                                                                 },
                                                        'PR': {'DataSet Name': None,
                                                               'TrainSet Location': None,
                                                               'TestSet Location': None,
                                                               'REG Model Location': None
                                                               }
                                                        }
                                             }
                               },
                     'darwin': {'CAT': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_355-ER-TestFold_1',
                                                          'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Train',
                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Test',
                                                          'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CAT_355_TF_1/model_data_Epoch_1000.pt'
                                                          },
                                                   'Her2': {'DataSet Name': r'FEATURES: Exp_392-Her2-TestFold_1',
                                                            'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/HER2/Ran_Exp_392-TestFold_1/Train',
                                                            'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/HER2/Ran_Exp_392-TestFold_1/Test',
                                                            'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data_from_gipdeep/runs/Ran_models/Her2/CAT_TF_1_392/model_data_Epoch_1000.pt'
                                                            },
                                                   'PR': {'DataSet Name': r'FEATURES: Exp_10-PR-TestFold_1',
                                                          'TrainSet Location': None,
                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_10-PR-TestFold_1/Test/',
                                                          'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/PR/CAT_10-PR-TF_1/model_data_Epoch_1000.pt'
                                                          }
                                                   },
                                        'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Exp_393-ER-TestFold_2',
                                                          'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_393-TestFold_2/Train',
                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_393-TestFold_2/Test',
                                                          'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CAT_393-ER-TF_2/model_data_Epoch_1000.pt'
                                                          },
                                                   'PR': {'DataSet Name': r'FEATURES: Exp_20063-PR-TestFold_2',
                                                          'TrainSet Location': None,
                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_20063-PR-TestFold_2/Test/',
                                                          'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/PR/CAT_20063-PR-TF_2/model_data_Epoch_1000.pt'
                                                          },
                                                   'Her2': {'DataSet Name': r'FEATURES: Exp_412-Her2-TestFold_2',
                                                            'TrainSet Location': None,
                                                            'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_412-TestFold_2/Test/',
                                                            'REG Model Location': None
                                                            }
                                                   },
                                        'Fold 3': {'ER': {'DataSet Name': r'FEATURES: Exp_472-ER-TestFold_3',
                                                          'TrainSet Location': None,
                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_472-ER-TestFold_3/Test/',
                                                          'REG Model Location': None
                                                          },
                                                   }
                                        },
                                'ABCTB': {'Fold 1': {'survival': {'DataSet Name': r'FEATURES: Exp_20094-survival-TestFold_1',
                                                                  'TrainSet Location': None,
                                                                  'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/survival/Ran_Exp_20094-survival-TestFold_1/Test/',
                                                                  'REG Model Location': None
                                                                  }
                                                     }
                                          },
                                'CAT with Location': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_355-ER-TestFold_1 With Locations',
                                                                        'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Train_with_location',
                                                                        'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Test_with_location',
                                                                        'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CAT_355_TF_1/model_data_Epoch_1000.pt'
                                                                        },
                                                                 'Her2': {'DataSet Name': r'FEATURES: Exp_392-Her2-TestFold_1 With Locations',
                                                                          'TrainSet Location': None,
                                                                          'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_392-TestFold_1_With_Location/Test/',
                                                                          'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data_from_gipdeep/runs/Ran_models/Her2/CAT_TF_1_392/model_data_Epoch_1000.pt'
                                                                          },
                                                                 'is_Tumor': {'DataSet Name': r'FEATURES: Exp_627-is_Tumor-TestFold_1 With Locations',
                                                                              'TrainSet Location': None,
                                                                              'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/is_Tumor/Ran_Exp_627-TestFold_1_With_Location/Test/',
                                                                              'REG Model Location': None
                                                                              },
                                                                 'PR': {'DataSet Name': r'FEATURES: Exp_10-PR-TestFold_1  With Locations',
                                                                        'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR_is_Tumor/Train',
                                                                        'TestSet Location': None,
                                                                        'REG Model Location': None
                                                                        },
                                                                 'is_Tumor_for_PR': {'DataSet Name': r'FEATURES: Exp_627-is_Tumor_for_PR-TestFold_1 With Locations',
                                                                                     'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/is_Tumor_for_PR/Train',
                                                                                     'TestSet Location': None,
                                                                                     'REG Model Location': None
                                                                                     },
                                                                 'ResNet34': {'DataSet Name': r'FEATURES: Extraction via ResNet 34 pretraind model',
                                                                              'TrainSet Location': None,
                                                                              'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Resnet34_pretrained_features/',
                                                                              'REG Model Location': None
                                                                              }
                                                                 }
                                                      },
                                'CARMEL': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_358-ER-TestFold_1',
                                                             'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_358-TestFold_1/Train',
                                                             'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_358-TestFold_1/Test',
                                                             'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CARMEL_358-TF_1/model_data_Epoch_1000.pt'
                                                             },
                                                      'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1',
                                                               'TrainSet Location': None,
                                                               'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_419-Ki67-TestFold_1/Test/',
                                                               'REG Model Location': None
                                                               },
                                                      'Her2': {'DataSet Name': None,
                                                               'TrainSet Location': None,
                                                               'TestSet Location': None,
                                                               'REG Model Location': None
                                                               },
                                                      'PR': {'DataSet Name': None,
                                                             'TrainSet Location': None,
                                                             'TestSet Location': None,
                                                             'REG Model Location': None
                                                             }
                                                      },
                                           'Fold 2': {'Ki67': {'DataSet Name': r'FEATURES: Exp_490-Ki67-TestFold_2',
                                                               'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_490-Ki67-TestFold_2/Train/',
                                                               'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_490-Ki67-TestFold_2/Test/',
                                                               'REG Model Location': None
                                                               }
                                                      }
                                           },
                                'CARMEL 9-11': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_355-ER-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                  'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Carmel9/',
                                                                                       'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Carmel10/',
                                                                                       'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/Carmel11/'
                                                                                       }
                                                                  },
                                                           'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                    'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_419-Ki67-TestFold_1/Carmel9/',
                                                                                         'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_419-Ki67-TestFold_1/Carmel10/',
                                                                                         'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_419-Ki67-TestFold_1/Carmel11/'
                                                                                         }
                                                                    },
                                                           'Her2': {'DataSet Name': r'FEATURES: Model From Exp_392-Her2-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                    'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_392-TestFold_1/Carmel9/',
                                                                                         'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_392-TestFold_1/Carmel10/',
                                                                                         'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_392-TestFold_1/Carmel11/'
                                                                                         }
                                                                    },
                                                           'PR': {'DataSet Name': r'FEATURES: Model From Exp_10-PR-TestFold_1, CARMEL ONLY Slides Batch 9-11',
                                                                  'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_10-PR-TestFold_1/Carmel9/',
                                                                                       'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_10-PR-TestFold_1/Carmel10/',
                                                                                       'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_10-PR-TestFold_1/Carmel11/'
                                                                                       }
                                                                  }
                                                           },
                                                'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_393-ER-TestFold_2, CARMEL ONLY Slides Batch 9-11',
                                                                  'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_393-TestFold_2/Carmel9/',
                                                                                       'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_393-TestFold_2/Carmel10/',
                                                                                       'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_393-TestFold_2/Carmel11/'
                                                                                       }
                                                                  },
                                                           'Her2': {'DataSet Name': r'FEATURES: Model From Exp_412-Her2-TestFold_2, CARMEL ONLY Slides Batch 9-11',
                                                                    'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_412-TestFold_2/Carmel9/',
                                                                                         'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_412-TestFold_2/Carmel10/',
                                                                                         'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Ran_Exp_412-TestFold_2/Carmel11/'
                                                                                       }
                                                                    },
                                                           'PR': {'DataSet Name': r'FEATURES: Model From Exp_20063-PR-TestFold_2, CARMEL ONLY Slides Batch 9-11',
                                                                  'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_20063-PR-TestFold_2/Carmel9/',
                                                                                       'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_20063-PR-TestFold_2/Carmel10/',
                                                                                       'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Ran_Exp_20063-PR-TestFold_2/Carmel11/'
                                                                                       }
                                                                  },
                                                           'Ki67': {'DataSet Name': r'FEATURES: Model From Exp_490-Ki67-TestFold_2, CARMEL ONLY Slides Batch 9-11',
                                                                    'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_490-Ki67-TestFold_2/Carmel9/',
                                                                                         'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_490-Ki67-TestFold_2/Carmel10/',
                                                                                         'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Ki67/Ran_Exp_490-Ki67-TestFold_2/Carmel11/'
                                                                                         }
                                                                    }
                                                           },
                                                'Fold 3': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_472-ER-TestFold_3, CARMEL ONLY Slides Batch 9-11',
                                                                  'TestSet Location': {'Carmel 9': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_472-ER-TestFold_3/Carmel9/',
                                                                                       'Carmel 10': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_472-ER-TestFold_3/Carmel10/',
                                                                                       'Carmel 11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_472-ER-TestFold_3/Carmel11/'
                                                                                       }
                                                                  }
                                                           }
                                                },
                                'HAEMEK': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Model From Exp_355-ER-TestFold_1, HAEMEK ONLY Slides',
                                                             'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_355-TestFold_1/HAEMEK/'
                                                             },
                                                      'Ki67': {'DataSet Name': r'FEATURES: Exp_419-Ki67-TestFold_1, HAEMEK ONLY Slides',
                                                               'TestSet Location': None
                                                               },
                                                      'Her2': {'DataSet Name': r'FEATURES: Model From Exp_392-Her2-TestFold_1, HAEMEK ONLY Slides',
                                                               'TestSet Location': None
                                                               },
                                                      'PR': {'DataSet Name': r'FEATURES: Model From Exp_20010-PR-TestFold_1, HAEMEK ONLY Slides',
                                                             'TestSet Location': None
                                                             }
                                                      }
                                           },
                                'TCGA_ABCTB->CARMEL': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_293-ER-TestFold_1',
                                                                         'TestSet Location': {'CARMEL': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/ABCTB_TCGA_>CARMEL/CARMEL1-8',
                                                                                              'CARMEL 9-11': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/ABCTB_TCGA_>CARMEL/CARMEL9-11'
                                                                                              }
                                                                         },
                                                                  'Her2': {'DataSet Name': r'FEATURES: Exp_308-Her2-TestFold_1',
                                                                           'TestSet Location': {'CARMEL': None,
                                                                                                'CARMEL 9-11': None
                                                                                                }
                                                                           },
                                                                  'PR': {'DataSet Name': r'FEATURES: Exp_309-PR-TestFold_1',
                                                                         'TestSet Location': {'CARMEL': None,
                                                                                              'CARMEL 9-11': None
                                                                                              }
                                                                         }
                                                                  }
                                                       },
                                'TCGA_ABCTB': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_293-ER-TestFold_1',
                                                                 'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/Train',
                                                                 'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_293-TestFold_1/Test',
                                                                 'REG Model Location': None
                                                                 },
                                                          'Her2': {'DataSet Name': r'FEATURES: Exp_308-Her2-TestFold_1',
                                                                   'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Fold_1/Train',
                                                                   'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/Her2/Fold_1/Test',
                                                                   'REG Model Location': None
                                                                   },
                                                          'PR': {'DataSet Name': r'FEATURES: Exp_309-PR-TestFold_1',
                                                                 'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Fold_1/Train',
                                                                 'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/PR/Fold_1/Test',
                                                                 'REG Model Location': None
                                                                 }
                                                          },
                                               'Fold 2': {'ER': {'DataSet Name': r'FEATURES: Exp_299-ER-TestFold_2',
                                                                 'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/ran_299-Fold_2/Train',
                                                                 'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/ran_299-Fold_2/Test',
                                                                 'REG Model Location': None
                                                                 },
                                                          'Her2': {'DataSet Name': None,
                                                                   'TrainSet Location': None,
                                                                   'TestSet Location': None,
                                                                   'REG Model Location': None
                                                                   },
                                                          'PR': {'DataSet Name': None,
                                                                 'TrainSet Location': None,
                                                                 'TestSet Location': None,
                                                                 'REG Model Location': None
                                                                 }
                                                          }
                                               },
                                'CARMEL_40': {'Fold 1': {'ER': {'DataSet Name': r'FEATURES: Exp_381-ER-TestFold_1',
                                                                'TrainSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_381-TestFold_1/Train',
                                                                'TestSet Location': r'/Users/wasserman/Developer/WSI_MIL/All Data/Features/ER/Ran_Exp_381-TestFold_1/Test',
                                                                'REG Model Location': r'/Users/wasserman/Developer/WSI_MIL/Data from gipdeep/runs/Ran_models/ER/CARMEL_381-TF_1/model_data_Epoch_1200.pt'
                                                                },
                                                         'Her2': {'DataSet Name': None,
                                                                  'TrainSet Location': None,
                                                                  'TestSet Location': None,
                                                                  'REG Model Location': None
                                                                  },
                                                         'PR': {'DataSet Name': None,
                                                                'TrainSet Location': None,
                                                                'TestSet Location': None,
                                                                'REG Model Location': None
                                                                }
                                                         }
                                              }
                                }
                     }



    if '+is_Tumor' in target:
        receptor = target.split('+')[0]
        if receptor == 'Her2':
            return All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)][receptor],\
                   All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)]['is_Tumor_for_Her2']

        elif receptor == 'PR':
            return All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)][receptor], \
                   All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)]['is_Tumor_for_PR']

        elif receptor == 'ER':
            return All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)]['ER_for_is_Tumor'], \
                   All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)]['is_Tumor_for_ER']

    else:
        return All_Data_Dict[sys.platform][train_DataSet]['Fold ' + str(test_fold)][target]


#RanS 24.1.22
#taken from https://discuss.pytorch.org/t/check-gradient-flow-in-network/15063
def plot_grad_flow(named_parameters):
    '''Plots the gradients flowing through different layers in the net during training.
    Can be used for checking for possible gradient vanishing / exploding problems.

    Usage: Plug this function in Trainer class after loss.backwards() as
    "plot_grad_flow(self.model.named_parameters())" to visualize the gradient flow'''
    from matplotlib.lines import Line2D
    ave_grads = []
    max_grads = []
    layers = []
    for n, p in named_parameters:
        if (p.requires_grad) and ("bias" not in n):
            layers.append(n)
            ave_grads.append(p.grad.abs().mean())
            max_grads.append(p.grad.abs().max())
    plt.bar(np.arange(len(max_grads)), max_grads, alpha=0.1, lw=1, color="c")
    plt.bar(np.arange(len(max_grads)), ave_grads, alpha=0.1, lw=1, color="b")
    plt.hlines(0, 0, len(ave_grads) + 1, lw=2, color="k")
    plt.xticks(range(0, len(ave_grads), 1), layers, rotation="vertical")
    plt.xlim(left=0, right=len(ave_grads))
    #plt.ylim(bottom=-0.001, top=0.02)  # zoom in on the lower gradient regions
    plt.ylim(bottom=-0.001, top=np.max(max_grads)*1.05)  # zoom in on the lower gradient regions
    plt.xlabel("Layers")
    plt.ylabel("average gradient")
    plt.title("Gradient flow")
    plt.grid(True)
    plt.legend([Line2D([0], [0], color="c", lw=4),
                Line2D([0], [0], color="b", lw=4),
                Line2D([0], [0], color="k", lw=4)], ['max-gradient', 'mean-gradient', 'zero-gradient'])
    plt.gcf().subplots_adjust(bottom=0.5)


def send_run_data_via_mail():
    if sys.platform != 'linux':  # Send mail only when running on server
        return
    else:
        import yagmail

    path_parts = os.getcwd().split('/')
    if 'womer' in path_parts:
        filename = '/home/womer/project/runs/run_data.xlsx'
        user = 'womer'

    elif 'rschley' in path_parts:
        filename = '/home/rschley/code/WSI_MIL/general_try4/runs/run_data.xlsx'
        user = 'rschley'

    elif 'sgils' in path_parts:
        filename = '/mnt/gipnetapp_public/sgils/ran/runs/run_data.xlsx'
        user = 'sgils'

    else:
        print('This user parameters are not defined. Email will not be sent')
        return

    yag = yagmail.SMTP('gipmed.python@gmail.com')
    yag.send(
        to='gipmed.python@gmail.com',
        subject=user + ': run_data.xlsx',
        attachments=filename,
    )

    print('email sent to gipmed.python@gmail.com')


def fix_output_dir_due_different_user(model_user_name: str, output_dir):
    if sys.platform != 'linux':
        return output_dir

    path_parts = os.getcwd().split('/')
    if 'womer' in path_parts:
        current_user = 'Omer'

    elif 'rschley' in path_parts:
        current_user = 'Ran'

    elif 'sglis' in path_parts:
        current_user = 'Gil'

    else:
        raise Exception('current user is not recognized')

    if model_user_name != current_user:
        if current_user == 'Omer':
            output_dir = os.path.join(r'/home/womer/project/runs', 'from_other_user', output_dir.split('/')[-1])

        elif current_user == 'Ran':
            output_dir = os.path.join(r'/home/rschley/code/WSI_MIL/general_try4/runs', 'from_other_user', output_dir.split('/')[-1])

        elif current_user == 'Gil':
            output_dir = os.path.join(r'/mnt/gipnetapp_public/sgils/ran/runs', 'from_other_user', output_dir.split('/')[-1])

        # Create the Path to which data will be saved:

        Path(output_dir).mkdir(parents=True)


    return output_dir


def concatenate_minibatch(minibatch, is_shuffle: bool = False):
    if minibatch[1] == 0:  # There is 1 combined dataset that includes all samples
        return minibatch[0]

    else:  # There are 2 dataset, one for Censored samples and one for not censored samples. Those 2 datasets need to be combined
        # The data in the combined dataset is divided into censored and not censored.
        #  We'll shuffle it:
        indices = list(range(minibatch[0]['Censored'].size(0) + minibatch[1]['Censored'].size(0)))
        if is_shuffle:
            shuffle(indices)

        temp_minibatch = {}
        for key in minibatch[0].keys():
            if type(minibatch[0][key]) == torch.Tensor:
                temp_minibatch[key] = torch.cat([minibatch[0][key], minibatch[1][key]], dim=0)[indices]

            elif type(minibatch[0][key]) == list:
                if key == 'Tile Locations':
                    x_locs = minibatch[0][key][0].tolist() + minibatch[1][key][0].tolist()
                    y_locs = minibatch[0][key][1].tolist() + minibatch[1][key][1].tolist()
                    temp_minibatch[key] = {'X': x_locs,
                                           'Y': y_locs
                                           }

                else:
                    minibatch[0][key].extend(minibatch[1][key])
                    minibatch[0][key] = [minibatch[0][key][i] for i in indices]

                    temp_minibatch[key] = minibatch[0][key]

            elif type(minibatch[0][key]) == dict:
                for inside_key in minibatch[0][key].keys():
                    minibatch[0][key][inside_key] = \
                    torch.cat([minibatch[0][key][inside_key], minibatch[1][key][inside_key]], dim=0)[indices]

                    temp_minibatch[key] = minibatch[0][key]

            else:
                raise Exception('Could not find the type for this data')

        return temp_minibatch


#####

def rgb2heb(imageRGB, color_values: list = None):

    """
    takes an RGB image of a slide stained by H&E,
    separates the colors into the components of the stains
    returns the imageHEB which containts H, E, and background images
    """

    if type(imageRGB) == Image.Image:
        imageRGB = np.asarray(imageRGB)

    # remove zeros by scaling
    imageRGB = (imageRGB.astype(np.uint16) + 1) / 256
    #imageRGB = (imageRGB + 1) / 256

    if color_values == None:
        # calibrated HE values with background
        # He = [0.49021345; 0.8398868; 0.2329828];
        # Eo = [0.15047029; 0.95457155; 0.2572001];
        # Res = [ 0.59782326 ;  0.6429284 ; 0.47880098 ]; % residual

        # default HE value
        # He = [0.6443186; 0.7166757; 0.26688856];
        # Eo = [0.09283128; 0.9545457; 0.28324];
        # Res = [ 0.63595444 ;  0.001 ; 0.7717266 ]; % residual

        # calibrated HE valued w/o background
        """He = np.array([0.49555248, 0.84, 0.22093])
        Eo = np.array([0.1617637, 0.94391227, 0.2878579])
        Res = np.array([0.6753781, 0.001, 0.7374717])  # residual"""


        # HE values from paper:
        He = np.array([0.18, 0.2, 0.08])
        Eo = np.array([0.01, 0.13, 0.01])
        Res = np.array([0.1, 0.21, 0.29])  # DAB

    else:
        He = -np.log10(np.array(color_values[0]) / 255)
        Eo = -np.log10(np.array(color_values[1]) / 255)
        Res = -np.log10(np.array(color_values[2]) / 255)


    # combine stain vectors to deconvolution matrix
    HEtoRGB = np.stack([He / np.linalg.norm(He), Eo / np.linalg.norm(Eo), Res / np.linalg.norm(Res)], axis=1)
    RGBtoHE = np.linalg.inv(HEtoRGB)

    # perform color deconvolution
    image_rgb_flattened = np.stack([imageRGB[:, :, 0].ravel(), imageRGB[:, :, 1].ravel(), imageRGB[:, :, 2].ravel()], axis=0)
    try:
        imageHEB_flattened = np.matmul(RGBtoHE, -np.log10(image_rgb_flattened))
    except RuntimeWarning:
        print('DEBUG ME !!!!')

    # Converting back to 3 channels:
    imageHEB = np.zeros_like(imageRGB)
    imageHEB[:, :, 0] = imageHEB_flattened[0].reshape(imageRGB.shape[0], imageRGB.shape[1])
    imageHEB[:, :, 1] = imageHEB_flattened[1].reshape(imageRGB.shape[0], imageRGB.shape[1])
    imageHEB[:, :, 2] = imageHEB_flattened[2].reshape(imageRGB.shape[0], imageRGB.shape[1])

    return imageHEB, HEtoRGB,


def heb2rgb(imageHEB, HEBtoRGB_Conversion_Matrix):
    # Dividing channels:
    """
    image_H = np.zeros_like(imageHEB)
    image_H[:, :, 0] = imageHEB[:, :, 0]
    image_H_flattened = np.stack([image_H[:, :, 0].ravel(), image_H[:, :, 1].ravel(), image_H[:, :, 2].ravel()], axis=0)
    """

    image_HEB_flattened = np.stack([imageHEB[:, :, 0].ravel(), imageHEB[:, :, 1].ravel(), imageHEB[:, :, 2].ravel()], axis=0)
    # convert to RGB:
    Optical_Density_flattened = np.matmul(HEBtoRGB_Conversion_Matrix, image_HEB_flattened)

    # unflatten:
    OD = np.zeros_like(imageHEB)
    OD[:, :, 0] = Optical_Density_flattened[0].reshape(imageHEB.shape[0], imageHEB.shape[1])
    OD[:, :, 1] = Optical_Density_flattened[1].reshape(imageHEB.shape[0], imageHEB.shape[1])
    OD[:, :, 2] = Optical_Density_flattened[2].reshape(imageHEB.shape[0], imageHEB.shape[1])

    # exp10 to go back from Optical Density to Intensity:
    image_RGB = np.power(10, -OD)

    # Clip the image to have values in the range 0-1:
    image_RGB = np.clip(image_RGB, 0, 1)

    return image_RGB


def cohort_to_int(cohort_list: list) -> list:
    CohortDictionary = {'ABCTB': 0,
                        'CARMEL1': 1,
                        'CARMEL2': 1,
                        'CARMEL3': 1,
                        'CARMEL4': 1,
                        'CARMEL5': 1,
                        'CARMEL6': 1,
                        'CARMEL7': 1,
                        'CARMEL8': 1,
                        'TCGA': 2,
                        'HAEMEK': 3,
                        'HAEMEK1': 3
                        }

    return [CohortDictionary[key] for key in cohort_list]


