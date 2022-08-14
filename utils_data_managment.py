"""
This file contains all functions needed for data Management, Pre Processing etc.
That includes:
- Organization in directories.
- Image segmentation.
- Grid production
"""
import Dataset_Maker.dataset_utils
import utils
import pandas as pd
from tqdm import tqdm
import os
import pickle
import numpy as np
from PIL import Image
from typing import List, Tuple
import openslide
import glob
import sys
import matplotlib.pyplot as plt
from shutil import copy2, copyfile
import matplotlib.patches as patches
from matplotlib.collections import PatchCollection
import cv2 as cv
import multiprocessing
from functools import partial
from datetime import date
import time
from colorsys import rgb_to_hsv
import Dataset_Maker.remove_control_tissue as remove_from_seg
from enum import Enum
Image.MAX_IMAGE_PIXELS = None


class OTSU_METHOD(Enum):
    OTSU3_FLEXIBLE_THRESH = 0
    OTSU3_LOWER_THRESH = 1
    OTSU3_UPPER_THRESH = 2
    OTSU_REGULAR = 3


def check_level1_mag():
    slide_files = glob.glob(os.path.join(r'/home/womer/project/All Data/TCGA', '*.svs'))
    l1 = np.zeros(len(slide_files))
    for ind, file in enumerate(tqdm(slide_files)):
        img = openslide.open_slide(file)
        try:
            l1[ind] = int(img.level_downsamples[1])
        except:
            print('failed to read, img.level_downsamples:', img.level_downsamples)
        if l1[ind]!=4:
            print('file:', file)
            print('l1:', l1[ind])


def herohe_slides2images():
    slide_files_mrxs = glob.glob(os.path.join('All Data', 'HEROHE', '*.mrxs'))
    for _, file in enumerate(tqdm(slide_files_mrxs)):
        file = file.split('/')[-1][:-5]
        slide_2_image(file)


def slide_2_image(slide_name: str, DataSet: str = 'HEROHE'):
    slide_file = os.path.join('All Data', DataSet, slide_name + '.mrxs')
    segMap_file = os.path.join('All Data', DataSet, 'SegData', 'SegMaps', slide_name + '_SegMap.png')

    segMap = np.array(Image.open(segMap_file))
    (rows, cols) = np.where(segMap == 255)
    min_row, max_row = rows.min(), rows.max()
    min_col, max_col = cols.min(), cols.max()

    img = openslide.open_slide(slide_file)
    scale = int(img.level_dimensions[0][1] / segMap.shape[0] / 2)

    top_left = (min_row * scale * 2, min_col * scale * 2)
    window_size = ((max_row - min_row) * scale, (max_col - min_col) * scale)

    image = img.read_region((top_left[1], top_left[0]), 1, (window_size[1], window_size[0])).convert('RGB')
    if not os.path.isdir(os.path.join('All Data', DataSet, 'images')):
        os.mkdir(os.path.join('All Data', DataSet, 'images'))

    image.save(os.path.join('All Data', DataSet, 'images', slide_name + '.jpg'))
    #image.save(os.path.join('All Data', DataSet, 'images', slide_name + '.png'))


def make_tiles_hard_copy(DataSet: str = 'TCGA',
                         ROOT_DIR: str = 'All Data',
                         tile_sz: int = 256,
                         num_tiles: int = -1,
                         desired_magnification: int = 10,
                         added_extension: str = '',
                         num_workers: int = 1,
                         oversized_HC_tiles: bool = False,
                         as_jpg: bool = False,
                         resize_tiles_to_size: int = None):
    """    
    :param DataSet: Dataset to create grid for 
    :param ROOT_DIR: Root Directory for the data
    :param tile_sz: Desired tile size at desired magnification.
    :param tissue_coverage: tissue percent requirement for each tile in the grid 
    :param added_extension: extension for the slides_data.xlsx file and all created sub-directories. this is needed in
           the case where we want to create alternative grids and keep the grids already created
    :return: 
    """""

    slides_data_file = os.path.join(ROOT_DIR, DataSet, 'slides_data_' + DataSet + '.xlsx')
    grid_data_file = os.path.join(ROOT_DIR, DataSet, 'Grids' + added_extension, 'Grid_data.xlsx')

    slide_meta_data_DF = pd.read_excel(slides_data_file)
    grid_meta_data_DF = pd.read_excel(grid_data_file)
    slides_meta_data_DF = pd.DataFrame({**slide_meta_data_DF.set_index('file').to_dict(),
                                 **grid_meta_data_DF.set_index('file').to_dict()})
    slides_meta_data_DF.reset_index(inplace=True)
    slides_meta_data_DF.rename(columns={'index': 'file'}, inplace=True)


    files = slides_meta_data_DF.loc[slides_meta_data_DF['id'] == DataSet]['file'].tolist()

    meta_data_DF = pd.DataFrame(files, columns=['file'])

    slides_meta_data_DF.set_index('file', inplace=True)
    meta_data_DF.set_index('file', inplace=True)

    print('Starting hard-copy tile production')
    print()

    with multiprocessing.Pool(num_workers) as pool:
        with tqdm(total=len(files)) as pbar:
            for i, _ in enumerate(pool.map(partial(_make_HC_tiles_from_slide,
                                                   meta_data_DF=slides_meta_data_DF,
                                                   ROOT_DIR=ROOT_DIR,
                                                   added_extension=added_extension,
                                                   DataSet=DataSet,
                                                   tile_size=tile_sz,
                                                   desired_magnification=desired_magnification,
                                                   num_tiles=num_tiles,
                                                   from_tile=0,
                                                   oversized_HC_tiles=oversized_HC_tiles,
                                                   as_jpg=as_jpg,
                                                   resize_tiles_to_size=resize_tiles_to_size),
                                           files)):
                pbar.update()
    '''
    for file in tqdm(files):
        _make_HC_tiles_from_slide(file=file,
                                  meta_data_DF=slides_meta_data_DF,
                                  ROOT_DIR=ROOT_DIR,
                                  added_extension=added_extension,
                                  DataSet=DataSet,
                                  tile_size=tile_sz,
                                  desired_magnification=desired_magnification,
                                  num_tiles=num_tiles,
                                  from_tile=0,
                                  oversized_HC_tiles=oversized_HC_tiles,
                                  as_jpg=as_jpg,
                                  resize_tiles_to_size=resize_tiles_to_size
                                  )
    '''

    print('Finished hard-copy tile production phase !')


def _make_HC_tiles_from_slide(file: str,
                              meta_data_DF: pd.DataFrame,
                              ROOT_DIR: str,
                              DataSet: str,
                              from_tile: int,
                              num_tiles: int,
                              added_extension: str = '',
                              desired_magnification: int = 20,
                              tile_size: int = 256,
                              oversized_HC_tiles: bool = False,
                              as_jpg: bool = False,
                              resize_tiles_to_size: int = None):
    start = time.time()
    if meta_data_DF.loc[file, 'Total tiles - ' + str(tile_size) + ' compatible @ X' + str(desired_magnification)] == -1:
        print('Could not find tile data for slide ' + file)
        return

    file_name = os.path.join(ROOT_DIR, DataSet, file)

    out_dir = os.path.join(ROOT_DIR, DataSet, 'tiles' + added_extension)

    objective_power = meta_data_DF.loc[file, 'Manipulated Objective Power']
    if objective_power == 'Missing Data':
        print('hard copy was not computed for file {}'.format(file))
        print('objective power was not found')
        return

    slide = openslide.OpenSlide(file_name)

    base_name = '.'.join((os.path.basename(file_name)).split('.')[:-1])
    basic_grid_file_name = base_name + '--tlsz' + str(tile_size) + '.data'

    # open grid list:
    grid_file = os.path.join(ROOT_DIR, DataSet, 'Grids' + added_extension, basic_grid_file_name)
    with open(grid_file, 'rb') as filehandle:
        # read the data as binary data stream
        grid_list = pickle.load(filehandle)

    if num_tiles == -1: #extract all tiles!
        num_tiles = len(grid_list)
    elif num_tiles > len(grid_list):
        num_tiles = len(grid_list)

    if not os.path.isdir(out_dir):
        os.mkdir(out_dir)
    if not os.path.isdir(os.path.join(out_dir, base_name)):
        os.mkdir(os.path.join(out_dir, base_name))
    # do not rewrite if all tiles are already saved
    elif len([name for name in os.listdir(os.path.join(out_dir, base_name)) if os.path.isfile(os.path.join(out_dir, base_name, name))]) == num_tiles + 1:
        print('skipping file ' + file + ", all tiles already exist")
        return

    best_slide_level, adjusted_tile_size, level_0_tile_size = \
        utils.get_optimal_slide_level(slide, objective_power, desired_magnification, tile_size)

    start1 = time.time()

    # initializing variables for  excel log file
    tile_names, tile_locations = [], []

    for ind, loc in enumerate(grid_list[from_tile: from_tile + num_tiles]):
        image_tile, _, _ = utils._get_tiles(slide=slide,
                                            locations=[loc],
                                            tile_size_level_0=level_0_tile_size,
                                            adjusted_tile_sz=adjusted_tile_size,
                                            output_tile_sz=tile_size,
                                            best_slide_level=best_slide_level,
                                            random_shift=False,
                                            oversized_HC_tiles=oversized_HC_tiles)


        if resize_tiles_to_size != None and resize_tiles_to_size != tile_size:
            image_tile[0] = image_tile[0].resize((resize_tiles_to_size, resize_tiles_to_size), resample=Image.BICUBIC)

        if as_jpg:
            tile_file_name = os.path.join(out_dir, base_name, 'tile_' + str(ind) + '.jpg')
            image_tile[0].save(tile_file_name, "JPEG")
        else:
            tile_file_name = os.path.join(out_dir, base_name, 'tile_' + str(ind) + '.data')
            tile_array = np.array(image_tile[0])
            with open(tile_file_name, 'wb+') as fh:
                fh.write('{0:} {1:} {2:} {3:}\n'.format(tile_array.dtype, tile_array.shape[0], tile_array.shape[1],
                                                            tile_array.shape[2]).encode('ascii'))
                fh.write(tile_array)

        # Create excel log file containing tile names and locations:
        tile_names.append('tile_' + str(ind))
        tile_locations.append(list(loc))

    excel_log_DF = pd.DataFrame({'Tile Names': tile_names,
                                 'Locations': tile_locations})
    excel_log_DF.to_csv(os.path.join(out_dir, base_name, 'log_file_tile_size_' + str(tile_size) + '.csv'))

    slide.close()

    end = time.time()

    if num_tiles > 0:
        print('finished file ' + file + ', total time ' + str(round(end-start, 2)) + ' sec, time per tile: ' + str(round((end - start1)/num_tiles, 2)) + ' sec')
    return


def _make_HC_tiles_from_slide_old(file_name: str, from_tile: int, num_tiles: int, tile_basic_file_name: str, magnification: int = 20, tile_size: int = 256):
    BASIC_OBJ_POWER = 20
    adjusted_tile_size = tile_size * (magnification // BASIC_OBJ_POWER)
    basic_grid_file_name = 'grid_tlsz' + str(adjusted_tile_size) + '.data'

    # open grid list:
    grid_file = os.path.join(file_name.split('/')[0], file_name.split('/')[1], basic_grid_file_name)
    with open(grid_file, 'rb') as filehandle:
        # read the data as binary data stream
        grid_list = pickle.load(filehandle)

    if not os.path.isdir(tile_basic_file_name):
        os.mkdir(tile_basic_file_name)
        os.mkdir(os.path.join(tile_basic_file_name, str(tile_size)))
    if not os.path.isdir(os.path.join(tile_basic_file_name, str(tile_size))):
        os.mkdir(os.path.join(tile_basic_file_name, str(tile_size)))

    for tile_idx in range(from_tile, from_tile + num_tiles):
        tile, _ = utils._get_tiles_2(file_name, [grid_list[tile_idx]], adjusted_tile_size)
        tile_file_name = os.path.join(tile_basic_file_name, str(tile_size), str(tile_idx) + '.data')
        with open(tile_file_name, 'wb') as filehandle:
            pickle.dump(tile, filehandle)


def compute_normalization_values(DataSet: str = 'HEROHE', ROOT_DIR: str = 'All Data', tile_size: int = 256) -> tuple:
    """
    This function runs over a set of images and compute mean and variance of each channel.
    The function computes these statistic values over the thumbnail images which are at X1 magnification
    :return:
    """
    #ROOT_DIR = 'All Data'
    #if DataSet == 'LUNG':
    #    ROOT_DIR = r'/home/rschley/All_Data/LUNG'#os.path.join('All Data', 'LUNG')

    stats_list =[]
    print('Computing image data set {} Mean and Variance...'.format(DataSet))
    meta_data = pd.read_excel(os.path.join(ROOT_DIR, 'slides_data.xlsx'))
    ### meta_data = meta_data.loc[meta_data['id'] == DataSet]

    meta_data = meta_data.loc[meta_data['id'] == DataSet]
    if DataSet == 'HEROHE':
        meta_data = meta_data.loc[meta_data['test fold idx'] == 1]
    files = meta_data['file'].tolist()
    meta_data.set_index('file', inplace=True)

    # gather tissue image values from thumbnail image using the segmentation map:
    for i, file in enumerate(tqdm(files)):
        filename = os.path.basename(file).split('.')[0]
        if meta_data.loc[[file], ['Total tiles - ' + str(tile_size) + ' compatible @ X20']].values[0][0] == -1:
            continue

        image_stats = {}
        thumb = np.array(Image.open(os.path.join(ROOT_DIR, DataSet, 'SegData/Thumbs', filename + '_thumb.png')))
        segMap = np.array(Image.open(os.path.join(ROOT_DIR, DataSet, 'SegData/SegMaps', filename + '_SegMap.png')))
        tissue = thumb.transpose(2, 0, 1) * (segMap > 0)  # SegMap positive is to 255
        tissue_pixels = (tissue[0] != 0).sum()
        tissue_matter = np.where(tissue[0] != 0)
        values = tissue[:, tissue_matter[0], tissue_matter[1]]
        image_stats['Pixels'] = tissue_pixels
        image_stats['Mean'] = values.mean(axis=1)
        image_stats['Var'] = values.var(axis=1)
        stats_list.append(image_stats)

    # Compute total mean and var:
    N = 0
    running_mean = 0
    running_mean_squared = 0
    running_var = 0
    for i, item in enumerate(stats_list):
        n = item['Pixels']
        if n > 0:  # avoid empty/missing slides
            N += n
            running_mean += item['Mean'] * n
            running_mean_squared += (item['Mean'] ** 2) * n
            running_var += item['Var'] * n

    total_mean = running_mean / N
    total_var = (running_mean_squared + running_var) / N - total_mean ** 2
    print('Finished computing statistical data over {} thumbnail slides'.format(i+1))
    print('Mean: {}'.format(total_mean))
    print('Variance: {}'.format(total_var))

    # Save data to file:
    try:
        with open(os.path.join(ROOT_DIR, 'ImageStatData.data'), 'wb') as filehandle:
            # store the data as binary data stream
            pickle.dump(stats_list, filehandle)
    except:
        print('unable to save normalization values')

    return total_mean, total_var


def make_grid(DataSet: str = 'TCGA',
              ROOT_DIR: str = 'All Data',
              tile_sz: int = 256,
              tissue_coverage: float = 0.5,
              desired_magnification: int = 10,
              added_extension: str = '',
              different_SegData_path_extension: str = '',
              num_workers: int = 1):
    """    
    :param DataSet: Dataset to create grid for 
    :param ROOT_DIR: Root Directory for the data
    :param tile_sz: Desired tile size at desired magnification.
    :param tissue_coverage: tissue percent requirement for each tile in the grid 
    :param added_extension: extension for the slides_data.xlsx file and all created sub-directories. this is needed in
           the case where we want to create alternative grids and keep the grids already created
    :param different_SegData_path_extension: a parameter defining the modified name of the SegData directory.
    :return: 
    """""

    slides_data_file = os.path.join(ROOT_DIR, DataSet, 'slides_data_' + DataSet + '.xlsx')

    slides_meta_data_DF = pd.read_excel(slides_data_file)
    files = slides_meta_data_DF.loc[slides_meta_data_DF['id'] == DataSet]['file'].tolist()

    meta_data_DF = pd.DataFrame(files, columns=['file'])

    slides_meta_data_DF.set_index('file', inplace=True)
    meta_data_DF.set_index('file', inplace=True)
    tile_nums = []
    total_tiles = []

    # Save the grid to file:
    grids_dir = os.path.join(ROOT_DIR, DataSet, 'Grids_' + str(desired_magnification) + added_extension)
    grid_images_dir = os.path.join(ROOT_DIR, DataSet, 'SegData' + different_SegData_path_extension,
                                      'GridImages_'  + str(desired_magnification) + '_' + str(tissue_coverage) + added_extension.replace('.', '_'))
    if not os.path.isdir(grids_dir):
        os.mkdir(grids_dir)
    if not os.path.isdir(grid_images_dir):
        os.mkdir(grid_images_dir)

    print('Starting Grid production...')
    print()

    if sys.platform == 'win32':
        debug = True
    else:
        debug = False

    if debug:
        for file in tqdm(files):
            tile_nums1, total_tiles1 = _make_grid_for_image(file=file,
                                         meta_data_DF=slides_meta_data_DF,
                                         ROOT_DIR=ROOT_DIR,
                                         different_SegData_path_extension=different_SegData_path_extension,
                                         tissue_coverage=tissue_coverage,
                                         tile_sz=tile_sz,
                                         desired_magnification=desired_magnification,
                                         grids_dir=grids_dir,
                                         grid_images_dir=grid_images_dir)
            tile_nums.append(tile_nums1)
            total_tiles.append(total_tiles1)
    else:
        with multiprocessing.Pool(num_workers) as pool:
            for tile_nums1, total_tiles1 in tqdm(pool.imap(partial(_make_grid_for_image,
                                                                   meta_data_DF=slides_meta_data_DF,
                                                                   ROOT_DIR=ROOT_DIR,
                                                                   different_SegData_path_extension=different_SegData_path_extension,
                                                                   tissue_coverage=tissue_coverage,
                                                                   tile_sz=tile_sz,
                                                                   desired_magnification=desired_magnification,
                                                                   grids_dir=grids_dir,
                                                                   grid_images_dir=grid_images_dir),
                                                           files), total=len(files)):
                tile_nums.append(tile_nums1)
                total_tiles.append(total_tiles1)

    # Adding the number of tiles to the excel file:
    #TODO - support adding grids to a half-filled excel files? (currently erases everything) - FIXED (but need to to complete evaluation)

    slide_usage = list(((np.array(tile_nums) / np.array(total_tiles)) * 100).astype(int))

    meta_data_DF.loc[files, 'Legitimate tiles - ' + str(tile_sz) + ' compatible @ X' + str(desired_magnification)] = tile_nums
    meta_data_DF.loc[files, 'Total tiles - ' + str(tile_sz) + ' compatible @ X' + str(desired_magnification)] = total_tiles
    meta_data_DF.loc[files, 'Slide tile usage [%] (for ' + str(tile_sz) + '^2 Pix/Tile) @ X' + str(desired_magnification)] = slide_usage
    meta_data_DF.loc[files, 'bad segmentation'] = ''

    meta_data_DF.to_excel(os.path.join(grids_dir, 'Grid_data.xlsx'))

    # Save Grids creation MetaData to file
    grid_productoin_meta_data_dict = {'Creation Date': str(date.today()),
                                      'Tissue Coverage': tissue_coverage,
                                      'Segmantation Path': os.path.join(ROOT_DIR, DataSet, 'SegData' + different_SegData_path_extension)
                                      }

    grid_production_DF = pd.DataFrame([grid_productoin_meta_data_dict]).transpose()
    grid_production_DF.to_excel(os.path.join(grids_dir, 'production_meta_data.xlsx'))

    print('Finished Grid production phase !')


def _make_grid_for_image(file, meta_data_DF, ROOT_DIR, different_SegData_path_extension,
                         tissue_coverage, tile_sz, desired_magnification, grids_dir, grid_images_dir):
    filename = '.'.join(os.path.basename(file).split('.')[:-1])
    database = meta_data_DF.loc[file, 'id']
    grid_file = os.path.join(grids_dir, filename + '--tlsz' + str(tile_sz) + '.data')
    segmap_file = os.path.join(ROOT_DIR, database, 'SegData' + different_SegData_path_extension, 'SegMaps',
                               filename + '_SegMap.png')

    if os.path.isfile(os.path.join(ROOT_DIR, database, file)) and os.path.isfile(segmap_file):  # make sure file exists
        height = int(meta_data_DF.loc[file, 'Height'])
        width = int(meta_data_DF.loc[file, 'Width'])

        objective_power = meta_data_DF.loc[file, 'Manipulated Objective Power']
        if objective_power == 'Missing Data':
            print('Grid was not computed for file {}'.format(file))
            print('objective power was not found')
            tile_nums = 0
            total_tiles = -1
            return tile_nums, total_tiles

        adjusted_tile_size_at_level_0 = int(tile_sz * (int(objective_power) / desired_magnification))
        basic_grid = [(row, col) for row in range(0, height, adjusted_tile_size_at_level_0) for col in
                      range(0, width, adjusted_tile_size_at_level_0)]
        #if database == 'TMA':
        #    basic_grid = [(0, 408)]

        total_tiles = len(basic_grid)

        # We now have to check, which tiles of this grid are legitimate, meaning they contain enough tissue material.
        legit_grid, out_grid = _legit_grid(segmap_file,
                                           basic_grid,
                                           adjusted_tile_size_at_level_0,
                                           (height, width),
                                           desired_tissue_coverage=tissue_coverage)
        # create a list with number of tiles in each file
        tile_nums = len(legit_grid)

        # Plot grid on thumbnail
        thumb_file_jpg = os.path.join(ROOT_DIR, database, 'SegData' + different_SegData_path_extension, 'Thumbs',
                                  filename + '_thumb.jpg')
        thumb_file_png = os.path.join(ROOT_DIR, database, 'SegData' + different_SegData_path_extension, 'Thumbs',
                                  filename + '_thumb.png')  # for old files

        grid_image_file = os.path.join(grid_images_dir, filename + '_GridImage.jpg')
        # do not rewrite
        if (os.path.isfile(thumb_file_jpg) or os.path.isfile(thumb_file_png)) and not os.path.isfile(grid_image_file):
            try:
                thumb = np.array(Image.open(thumb_file_jpg))
            except:
                thumb = np.array(Image.open(thumb_file_png))
            #slide = openslide.OpenSlide(os.path.join(ROOT_DIR, database, file))
            slide = openslide.open_slide(os.path.join(ROOT_DIR, database, file))
            thumb_downsample = slide.dimensions[0] / thumb.shape[1]  # shape is transposed
            patch_size_thumb = adjusted_tile_size_at_level_0 / thumb_downsample

            #start = time.time()
            fig, ax = plt.subplots()
            ax.imshow(thumb)
            patch_list = []
            for patch in out_grid:
                xy = (np.array(patch[::-1]) / thumb_downsample)
                rect = patches.Rectangle(xy, patch_size_thumb, patch_size_thumb, linewidth=1, edgecolor='none',
                                         facecolor='g', alpha=0.5)
                patch_list.append(rect)
            p = PatchCollection(patch_list, alpha=0.5, facecolors='g')
            ax.add_collection(p)
            #end = time.time()
            #print('file ' + file + 'new time:' + str(end-start))

            plt.axis('off')
            plt.savefig(grid_image_file,
                        bbox_inches='tight', pad_inches=0, dpi=400)
            plt.close(fig)


        with open(grid_file, 'wb') as filehandle:
            # store the data as binary data stream
            pickle.dump(legit_grid, filehandle)
    else:
        print('Grid was not computed for file {}'.format(file))
        if ~os.path.isfile(os.path.join(ROOT_DIR, database, file)):
            print('slide was not found')
        if ~os.path.isfile(segmap_file):
            print('seg map was not found')
        tile_nums = 0
        total_tiles = -1
    return tile_nums, total_tiles


def _legit_grid(image_file_name: str,
                grid: List[Tuple],
                adjusted_tile_size_at_level_0: int,
                size: tuple,
                desired_tissue_coverage: float = 0.5) -> List[Tuple]:
    """
    This function gets a .svs file name, a basic grid and adjusted tile size and returns a list of legitimate grid locations.
    :param image_file_name: .svs file name
    :param grid: basic grid
    :param adjusted_tile_size_at_level_0: adjusted tile size at level 0 of the slide
    :param size: size of original image (height, width)
    :param tissue_coverage: Coverage of tissue to make the slide legitimate
    :return:
    """
    non_legit_grid_tiles = []
    # Check if coverage is a number in the range (0, 1]
    if not (desired_tissue_coverage > 0 and desired_tissue_coverage <= 1):
        raise ValueError('Coverage Parameter should be in the range (0,1]')

    # open the segmentation map image from which the coverage will be calculated:
    segMap = np.array(Image.open(image_file_name))
    row_ratio = size[0] / segMap.shape[0]
    col_ratio = size[1] / segMap.shape[1]

    # the complicated next line only only rounds up the numbers
    tile_size_at_segmap_magnification = (int(-(-adjusted_tile_size_at_level_0 // row_ratio)), int(-(-adjusted_tile_size_at_level_0 // col_ratio)))
    # computing the compatible grid for the small segmentation map:
    idx_to_remove =[]
    for idx, (row, col) in enumerate(grid):
        new_row = int(-(-(row // row_ratio)))
        new_col = int(-(-(col // col_ratio)))

        # collect the data from the segMap:
        tile = segMap[new_row : new_row + tile_size_at_segmap_magnification[0], new_col : new_col + tile_size_at_segmap_magnification[1]]
        num_tile_pixels = tile_size_at_segmap_magnification[0] * tile_size_at_segmap_magnification[1]
        tissue_coverage = tile.sum() / num_tile_pixels / 255
        if tissue_coverage < desired_tissue_coverage:
            idx_to_remove.append(idx)

    # We'll now remove items from the grid. starting from the end to the beginning in order to keep the indices correct:
    for idx in reversed(idx_to_remove):
        #grid.pop(idx)
        non_legit_grid_tiles.append(grid.pop(idx))

    return grid, non_legit_grid_tiles


def make_slides_xl_file(DataSet: str = 'HEROHE', ROOT_DIR: str = 'All Data', out_path: str = ''):
    """
    This function goes over all directories and makes a table with slides data:
    (1) id
    (2) file name
    (3) ER, PR, Her2 status
    (4) size of image
    (5) MPP (Microns Per Pixel)
    It also erases all 'log' subdirectories
    :return:
    """

    SLIDES_DATA_FILE = 'slides_data_' + DataSet + '.xlsx'
    META_DATA_FILE = {}
    META_DATA_FILE['TCGA'] = 'TCGA_BRCA.xlsx'
    META_DATA_FILE['HEROHE'] = 'HEROHE_HER2_STATUS.xlsx'
    META_DATA_FILE['PORTO_HE'] = 'LISTA COMPLETA pdl1 - Gil - V3_batch1+2.xlsx'
    META_DATA_FILE['PORTO_PDL1'] = 'LISTA COMPLETA pdl1 - Gil - V3_batch1+2.xlsx'
    META_DATA_FILE['ABCTB'] = 'ABCTB_Path_Data1.xlsx'
    META_DATA_FILE['SHEBA'] = 'SHEBA ONCOTYPE 30_12_2021_Ran.xlsx'

    data_file = os.path.join(out_path, DataSet, SLIDES_DATA_FILE)
    new_file = False if os.path.isfile(data_file) else True

    if DataSet[:6]=='CARMEL':
        DataSet_key = 'CARMEL'
    elif DataSet[:5]=='SHEBA':
        DataSet_key = 'SHEBA'
    else:
        DataSet_key = DataSet

    #meta_data_DF = pd.read_excel(os.path.join(ROOT_DIR, DataSet, META_DATA_FILE[DataSet_key]))
    try:
        meta_data_DF = pd.read_excel(os.path.join(ROOT_DIR, META_DATA_FILE[DataSet_key]))  # barcode list moved to main data folder
        barcode_list_format = False
    except:
        try:
            meta_data_DF = pd.read_excel(os.path.join(ROOT_DIR, 'barcode_list_' + DataSet +'.xlsx'))
        except:
            meta_data_DF = pd.read_excel(os.path.join(ROOT_DIR, 'barcode_list.xlsx'))  # default
        barcode_list_format = True

    if DataSet == 'PORTO_HE':
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['SlideName'].astype(str)
    elif DataSet == 'PORTO_PDL1':
        slides_name = meta_data_DF['SlideName'].astype(str)
        batch_list = meta_data_DF['Batch']
        slide_list = []

        for slide, b in zip(slides_name, batch_list):
            if b == 1:
                try:
                    slide_start = int(slide[:4])
                except:
                    slide_start = 0
                try:
                    slide_end = int(slide[-2:])
                except:
                    slide_end = 0

                if 2015 < slide_start < 2025 and slide[4]=='-':
                    pdl_name = str(int(slide[5:])) + '-' + str(slide_start-2000)  # + ' PDL1'
                    slide_list.append(pdl_name)
                elif 15 < slide_end < 21 and slide[-3]=='-':
                    pdl_name = str(int(slide[:-3])) + '-' + str(slide_end)# + ' PDL1'
                    slide_list.append(pdl_name)
                else:
                    slide_list.append(slide)
            elif b == 2:
                slide_list.append(slide)
            else:
                ValueError('invalid batch number!')
        meta_data_DF['bcr_patient_barcode'] = slide_list

    elif DataSet == 'ABCTB':
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['Image File'].astype(str)
    elif DataSet == 'LEUKEMIA':
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['MarrowID'].astype(str)
    elif (DataSet == 'TCGA_LUNG') or (DataSet == 'SHEBA'):
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['PatientID'].astype(str)
    elif barcode_list_format:
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['SlideID'].astype(str)
    else:
        meta_data_DF['bcr_patient_barcode'] = meta_data_DF['bcr_patient_barcode'].astype(str)
    meta_data_DF.set_index('bcr_patient_barcode', inplace=True)

    if new_file:
        print('Creating a new data file in \'{}\''.format(data_file))
    else:
        print('Adding data to file \'{}\''.format(data_file))
        slides_data_DF = pd.read_excel(data_file)
        try:
            slides_data_DF.drop(labels='Unnamed: 0', axis='columns',  inplace=True)
        except KeyError:
            pass

    id_list = []

    slide_files_svs = glob.glob(os.path.join(ROOT_DIR, DataSet, '*.svs'))
    slide_files_ndpi = glob.glob(os.path.join(ROOT_DIR, DataSet, '*.ndpi'))
    slide_files_mrxs = glob.glob(os.path.join(ROOT_DIR, DataSet, '*.mrxs'))
    slide_files_tiff = glob.glob(os.path.join(ROOT_DIR, DataSet, '*.tiff'))
    slide_files_jpg = glob.glob(os.path.join(ROOT_DIR, DataSet, '*.jpg'))
    slides = slide_files_svs + slide_files_ndpi + slide_files_mrxs + slide_files_tiff + slide_files_jpg
    mag_dict = {'.svs': 'aperio.AppMag', '.ndpi': 'hamamatsu.SourceLens', '.mrxs': 'openslide.objective-power',
                'tiff': 'tiff.Software'}  # dummy for TIFF
    mpp_dict = {'.svs': 'aperio.MPP', '.ndpi': 'openslide.mpp-x', '.mrxs': 'openslide.mpp-x', 'tiff': 'openslide.mpp-x'}
    date_dict = {'.svs': 'aperio.Date', '.ndpi': 'tiff.DateTime', '.mrxs': 'mirax.GENERAL.SLIDE_CREATIONDATETIME',
                 'tiff': 'philips.DICOM_ACQUISITION_DATETIME'}

    for idx, file in enumerate(tqdm(slides)):
        fn, data_format = os.path.splitext(os.path.basename(file))
        id_dict = {}

        # Create a dictionary to the files and id's:
        if DataSet == 'TCGA':
            id_dict['patient barcode'] = '-'.join(file.split('/')[-1].split('-')[0:3])
        elif DataSet == 'ABCTB':
            id_dict['patient barcode'] = os.path.basename(file)
        elif DataSet == 'PORTO_PDL1':
            id_dict['patient barcode'] = '.'.join(os.path.basename(file).split('.')[:-1])[:-5]
        elif DataSet == 'PORTO_HE':
            file_name = '.'.join(os.path.basename(file).split('.')[:-1])
            if file_name[-3:] == ' he':
                file_name = file_name[:-3]
            if (file_name[-2:] == '-1') or (file_name[-2:] == '-2'):
                file_name = file_name[:-2]
            id_dict['patient barcode'] = file_name
        else:
            id_dict['patient barcode'] = '.'.join(os.path.basename(file).split('.')[:-1])

        id_dict['id'] = DataSet
        id_dict['file'] = os.path.basename(file)
        id_dict['DX'] = True if (file.find('DX') != -1 or DataSet != 'TCGA') else False

        # Get some basic data about the image like MPP (Microns Per Pixel) and size:
        try:
            img = openslide.open_slide(file)
        except:
            print('failed to read slide: ', file)
            continue
        try:
            #id_dict['MPP'] = float(img.properties['aperio.MPP'])
            id_dict['MPP'] = float(img.properties[mpp_dict[data_format]])
        except:
            id_dict['MPP'] = 'Missing Data'
        try:
            id_dict['Width'] = int(img.dimensions[0])
        except:
            id_dict['Width'] = 'Missing Data'
        try:
            id_dict['Height'] = int(img.dimensions[1])
        except:
            id_dict['Height'] = 'Missing Data'
        try:
            id_dict['Objective Power'] = int(float(img.properties[mag_dict[data_format]]))  # "manipulated" needs to be manual
        except:
            id_dict['Objective Power'] = 'Missing Data'
        try:
            id_dict['Scan Date'] = img.properties[date_dict[data_format]]
        except:
            id_dict['Scan Date'] = 'Missing Data'
        img.close()

        # Get data from META_DATA_FILE and add to the dictionary ER_status, PR_status, Her2_status
        if DataSet == 'PORTO_HE' or DataSet == 'PORTO_PDL1':
            try:
                value = meta_data_DF.loc[[id_dict['patient barcode']], ['PDL1']].values[0][0]
                if value == 0:
                    id_dict['PDL1 status'] = 'Negative'
                elif value == 1:
                    id_dict['PDL1 status'] = 'Positive'
                else:
                    id_dict['PDL1 status'] = 'Missing Data'
            except:
                id_dict['PDL1 status'] = 'Missing Data'
            try:
                value = meta_data_DF.loc[[id_dict['patient barcode']], ['EGFR']].values[0][0]
                if value == 0:
                    id_dict['EGFR status'] = 'Negative'
                elif value == 1:
                    id_dict['EGFR status'] = 'Positive'
                else:
                    id_dict['EGFR status'] = 'Missing Data'

            except:
                id_dict['EGFR status'] = 'Missing Data'

            try:
                id_dict['Origin'] = meta_data_DF.loc[[id_dict['patient barcode']], ['Origin']].values[0][0]
            except:
                id_dict['Origin'] = 'Missing Data'

        else:  # breast cancer
            try:
                id_dict['ER status'] = meta_data_DF.loc[[id_dict['patient barcode']], ['ER_status']].values[0][0]
            except:
                id_dict['ER status'] = 'Missing Data'
            try:
                id_dict['PR status'] = meta_data_DF.loc[[id_dict['patient barcode']], ['PR_status']].values[0][0]
            except:
                id_dict['PR status'] = 'Missing Data'
            try:
                id_dict['Her2 status'] = meta_data_DF.loc[[id_dict['patient barcode']], ['Her2_status']].values[0][0]
            except:
                id_dict['Her2 status'] = 'Missing Data'

        try:
            id_dict['test fold idx'] = meta_data_DF.loc[[id_dict['patient barcode']], ['Test_fold_idx']].values[0][0]
        except:
            id_dict['test fold idx'] = 'Missing Data'

        id_list.append(id_dict)

    if new_file:
        slides_data_DF = pd.DataFrame(id_list)
        messege_prefix = 'Creating'
    else:
        slides_data_DF = slides_data_DF.append(id_list)
        messege_prefix = 'Updated'

    slides_data_DF.to_excel(data_file)
    print('{} data file \'{}\''.format(messege_prefix, data_file))


def make_segmentations(DataSet: str = 'TCGA', ROOT_DIR: str = 'All Data', rewrite: bool = False, magnification: int = 1,
                       out_path: str = 'All Data', num_workers: int = 1):

    data_path = os.path.join(ROOT_DIR, DataSet)
    out_path_dataset = os.path.join(out_path, DataSet)
    print('Making Segmentation Maps for each slide file at location: {}'.format(data_path))

    if not os.path.isdir(out_path_dataset):
        os.mkdir(out_path_dataset)
    if not os.path.isdir(os.path.join(out_path_dataset, 'SegData')):
        os.mkdir(os.path.join(out_path_dataset, 'SegData'))
    if not os.path.isdir(os.path.join(out_path_dataset, 'SegData', 'Thumbs')):
        os.mkdir(os.path.join(out_path_dataset, 'SegData', 'Thumbs'))
    if not os.path.isdir(os.path.join(out_path_dataset, 'SegData', 'SegMaps')):
        os.mkdir(os.path.join(out_path_dataset, 'SegData', 'SegMaps'))
    if not os.path.isdir(os.path.join(out_path_dataset, 'SegData', 'SegImages')):
        os.mkdir(os.path.join(out_path_dataset, 'SegData', 'SegImages'))
    # Copy Code files into the segmentation directory:
    if not os.path.isdir(os.path.join(out_path_dataset, 'SegData', 'Code')):
        os.mkdir(os.path.join(out_path_dataset, 'SegData', 'Code'))
        # Get all .py files in the code path:
        code_files_path = os.path.join(out_path_dataset, 'SegData', 'Code')
        py_files = glob.glob('*.py')
        for _, file in enumerate(py_files):
            copyfile(file, os.path.join(code_files_path,os.path.basename(file)))

    slide_files_svs = glob.glob(os.path.join(data_path, '*.svs'))
    slide_files_ndpi = glob.glob(os.path.join(data_path, '*.ndpi'))
    slide_files_mrxs = glob.glob(os.path.join(data_path, '*.mrxs'))
    slide_files_jpg = glob.glob(os.path.join(data_path, '*.jpg'))
    slide_files_tiff = glob.glob(os.path.join(data_path, '*.tiff'))
    slide_files_tif = glob.glob(os.path.join(data_path, '*.tif'))
    slide_files = slide_files_svs + slide_files_ndpi + slide_files_mrxs + slide_files_jpg + slide_files_tiff + slide_files_tif
    print('found ' + str(len(slide_files)) + ' slides')

    slides_data_file = os.path.join(data_path, 'slides_data_' + DataSet + '.xlsx')
    slides_meta_data_DF = pd.read_excel(slides_data_file)
    slides_meta_data_DF.set_index('file', inplace=True)

    error_list = []

    if sys.platform == 'win32':
        debug = True
    else:
        debug = False

    if debug:
        for file in tqdm(slide_files):
            error1 = _make_segmentation_for_image(file, DataSet, rewrite, out_path_dataset, slides_meta_data_DF, magnification)
            if error1 != []:
                error_list.append(error1)
    else:
        with multiprocessing.Pool(num_workers) as pool:
            for error1 in tqdm(pool.imap(partial(_make_segmentation_for_image,
                                                           DataSet=DataSet,
                                                           magnification=magnification,
                                                           slides_meta_data_DF=slides_meta_data_DF,
                                                           rewrite=rewrite,
                                                           out_path_dataset=out_path_dataset),
                                                    slide_files), total=len(slide_files)):
                if error1 != []:
                    error_list.append(error1)

    if len(error_list) != 0:
        # Saving all error data to excel file:
        error_DF = pd.DataFrame(error_list)
        error_DF.to_excel(os.path.join(out_path, 'Segmentation_Errors.xlsx'))
        print('Segmentation Process finished WITH EXCEPTIONS!!!!')
        print('Check \"{}\" file for details...'.format(os.path.join(out_path, 'Segmentation_Errors.xlsx')))
    else:
        print('Segmentation Process finished without exceptions!')


def _make_segmentation_for_image(file, DataSet, rewrite, out_path_dataset, slides_meta_data_DF, magnification):
    fn, data_format = os.path.splitext(os.path.basename(file))
    data_dir = os.path.dirname(out_path_dataset)
    if not rewrite:
        pic1 = os.path.exists(os.path.join(out_path_dataset, 'SegData', 'Thumbs', fn + '_thumb.jpg'))
        pic2 = os.path.exists(os.path.join(out_path_dataset, 'SegData', 'SegMaps', fn + '_SegMap.png'))
        pic3 = os.path.exists(os.path.join(out_path_dataset, 'SegData', 'SegImages', fn + '_SegImage.jpg'))
        if pic1 and pic2 and pic3:
            return []

    slide = None
    try:
        slide = openslide.open_slide(file)
    except:
        print('Cannot open slide at location: {}'.format(file))

    if slide is not None:
        # Get a thumbnail image to create the segmentation for:
        objective_pwr = slides_meta_data_DF.loc[os.path.basename(file), 'Manipulated Objective Power']

        height = slide.dimensions[1]
        width = slide.dimensions[0]
        try:
            try:
                thumb = slide.get_thumbnail(
                    (width / (objective_pwr / magnification), height / (objective_pwr / magnification)))
            except:  # out of memory on personal laptop
                thumb = slide.get_thumbnail(
                    (width / (8 * objective_pwr / magnification), height / (8 * objective_pwr / magnification)))
        except openslide.lowlevel.OpenSlideError as err:
            error_dict = {}
            e = sys.exc_info()
            error_dict['File'] = file
            error_dict['Error'] = err
            error_dict['Error Details 1'] = e[0]
            error_dict['Error Details 2'] = e[1]
            print('Exception for file {}'.format(file))
            return error_dict

        # ignore black background regions at jpg images by turning them white
        if DataSet == 'RedSquares':
            thumb_arr = np.array(thumb)
            thumb_arr_equal1 = np.equal(thumb_arr[:, :, 0], thumb_arr[:, :, 1])
            thumb_arr_equal2 = np.equal(thumb_arr[:, :, 0], thumb_arr[:, :, 2])
            thumb_arr[thumb_arr_equal1 & thumb_arr_equal2, :] = 255
            thumb = Image.fromarray(thumb_arr)

        if DataSet == 'PORTO_PDL1' or DataSet[:4].casefold() == 'HER2'.casefold():
            is_IHC_slide = True
        else:
            is_IHC_slide = False

        thumb = remove_from_seg.remove_control_tissue_according_to_dataset(thumb, is_IHC_slide, fn, DataSet, data_dir)
        thumb = remove_from_seg.remove_slide_artifacts_according_to_dataset(thumb, DataSet)

        # if DataSet == 'ABCTB':
        if DataSet == 'PORTO_HE' or DataSet[:4] == 'HER2' or DataSet[:5] == 'SHEBA':
            otsu_method = OTSU_METHOD.OTSU3_FLEXIBLE_THRESH
        else:
            otsu_method = OTSU_METHOD.OTSU_REGULAR
        if DataSet in ['LEUKEMIA', 'AML']:
            thmb_seg_map, edge_image = _calc_simple_segmentation_for_image(thumb, magnification, white_thresh=250)
        elif DataSet[:3] == 'TMA':
            thmb_seg_map, edge_image = _calc_simple_segmentation_for_image(thumb, magnification, white_thresh=230)
        else:
            thmb_seg_map, edge_image = _calc_segmentation_for_image(thumb, magnification, otsu_method=otsu_method, is_IHC_slide=is_IHC_slide)
        slide.close()
        thmb_seg_image = Image.blend(thumb, edge_image, 0.5)

        # Saving segmentation map, segmentation image and thumbnail:
        thumb.save(os.path.join(out_path_dataset, 'SegData', 'Thumbs', fn + '_thumb.jpg'))
        thmb_seg_map.save(os.path.join(out_path_dataset, 'SegData', 'SegMaps', fn + '_SegMap.png'))
        thmb_seg_image.save(
            os.path.join(out_path_dataset, 'SegData', 'SegImages', fn + '_SegImage.jpg'))
    else:
        print('Error: Found no slide in path {}'.format(dir))
        # TODO: implement a case for a slide that cannot be opened.
        error_dict = {}
        error_dict['File'] = file
        error_dict['Error'] = 'Slide not found'
        return error_dict


def otsu3(img):
    hist = cv.calcHist([img], [0], None, [256], [0, 256])
    hist_norm = hist.ravel()/hist.sum()
    Q = hist_norm.cumsum()
    bins = np.arange(256)
    fn_min = np.inf
    thresh = -1,-1
    for i in range(1,256):
        for j in range(i+1, 256):
            p1, p2, p3 = np.hsplit(hist_norm,[i,j])  # probabilities
            q1, q2, q3 = Q[i], Q[j]-Q[i], Q[255]-Q[j]  # cum sum of classes
            if q1 < 1.e-6 or q2 < 1.e-6 or q3 < 1.e-6:
                continue
            b1, b2, b3 = np.hsplit(bins,[i,j]) # weights
            # finding means and variances
            m1, m2, m3 = np.sum(p1*b1)/q1, np.sum(p2*b2)/q2, np.sum(p3*b3)/q3
            v1, v2, v3 = np.sum(((b1-m1)**2)*p1)/q1,np.sum(((b2-m2)**2)*p2)/q2, np.sum(((b3-m3)**2)*p3)/q3
            # calculates the minimization function
            fn = v1*q1 + v2*q2 + v3*q3
            if fn < fn_min:
                fn_min = fn
                thresh = i,j
    return thresh


def _get_image_maxima(image, threshold=0.5, neighborhood_size=5):
    import scipy.ndimage as ndimage
    import scipy.ndimage.filters as filters

    data_max = filters.maximum_filter(image, neighborhood_size)
    maxima = (image == data_max)
    data_min = filters.minimum_filter(image, neighborhood_size)
    diff = ((data_max - data_min) > threshold)
    maxima[diff == 0] = 0

    labeled, num_objects = ndimage.label(maxima)
    xy = np.array(ndimage.center_of_mass(image, labeled, range(1, num_objects + 1)))
    return xy


def _calc_simple_segmentation_for_image(image: Image, magnification: int, white_thresh: int) -> (Image, Image):
    """
    This function creates a segmentation map for an Image
    :param magnification:
    :return:
    """

    # take all pixels that aren't (almost) completely white
    image_array = np.array(image)
    image_is_white = np.prod(image_array, axis=2) > white_thresh ** 3
    seg_map = np.ones_like(image_array)*255
    seg_map[image_is_white] = 0
    seg_map_PIL = Image.fromarray(seg_map)

    edge_image = cv.Canny(seg_map, 1, 254)
    # Make the edge thicker by dilating:
    kernel_dilation = np.ones((3, 3))
    edge_image = Image.fromarray(cv.dilate(edge_image, kernel_dilation, iterations=magnification * 2)).convert('RGB')

    return seg_map_PIL, edge_image


def _calc_segmentation_for_image(image: Image, magnification: int, otsu_method: OTSU_METHOD,
                                 is_IHC_slide: bool) -> (Image, Image):
    """
    This function creates a segmentation map for an Image
    """

    image_array = np.array(image.convert('CMYK'))[:, :, 1]

    # turn almost total black into total white to ignore black areas in otsu
    image_array_rgb = np.array(image)
    image_is_black = np.prod(image_array_rgb, axis=2) < 20**3
    image_array[image_is_black] = 0

    # otsu Thresholding:
    if (otsu_method == OTSU_METHOD.OTSU3_FLEXIBLE_THRESH) or (otsu_method == OTSU_METHOD.OTSU3_LOWER_THRESH):
        # 3way binarization
        thresh = otsu3(image_array)
        _, seg_map = cv.threshold(image_array, thresh[0], 255, cv.THRESH_BINARY)
    elif otsu_method == OTSU_METHOD.OTSU3_UPPER_THRESH:
        thresh = otsu3(image_array)
        _, seg_map = cv.threshold(image_array, thresh[1], 255, cv.THRESH_BINARY)
    else:
        _, seg_map = cv.threshold(image_array, 0, 255, cv.THRESH_OTSU)

    # test median pixel color to inspect segmentation
    if (otsu_method == OTSU_METHOD.OTSU3_FLEXIBLE_THRESH) and not is_IHC_slide:
        pixel_vec = image_array_rgb.reshape(-1, 3)[seg_map.reshape(-1)>0]
        #median_color = np.median(pixel_vec, axis=0)
        #median_hue = rgb_to_hsv(*median_color/256)[0]*360

        hue_vec = np.array([rgb_to_hsv(*pixel/256)[0]*360 for pixel in pixel_vec])
        #saturation_vec = np.array([rgb_to_hsv(*pixel / 256)[1] for pixel in pixel_vec])
        median_hue = np.median(hue_vec)
        #median_saturation = np.median(saturation_vec)
        #saturation_lower_decile = np.percentile(saturation_vec, 10)
        #if all(median_color > 180): #median pixel is white-ish, changed from 200
        #if (median_hue < 250) or (median_saturation < 0.15):  # median seg hue is not purple/red or saturation is very low (white)
        #if (median_hue < 250) or (saturation_lower_decile < 0.1):  # median seg hue is not purple/red or saturation is very low (white)
        take_upper_otsu3_thresh = median_hue < 250
        if take_upper_otsu3_thresh:  # median seg hue is not purple/red or saturation is very low (white)
            #take upper threshold
            thresh = otsu3(image_array)
            _, seg_map = cv.threshold(image_array, thresh[1], 255, cv.THRESH_BINARY)

    # Smoothing the tissue segmentation imaqe:
    size = 10 * magnification
    kernel_smooth = np.ones((size, size), dtype=np.float32) / size ** 2
    seg_map_filt = cv.filter2D(seg_map, -1, kernel_smooth)

    th_val = 5
    seg_map_filt[seg_map_filt > th_val] = 255
    seg_map_filt[seg_map_filt <= th_val] = 0

    if np.sum(seg_map_filt) == 0:  # handling empty TMA images
        seg_map_PIL = Image.fromarray(seg_map_filt)
        edge_image = seg_map_PIL.convert('RGB')
        return seg_map_PIL, edge_image

    if not is_IHC_slide:
        # find small contours and delete them from segmentation map
        size_thresh = 5000  # lung cancer biopsies can be very small
        contours, _ = cv.findContours(seg_map_filt, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        contour_area = np.zeros(len(contours))
        rectangle_area = np.zeros(len(contours))

        for ii in range(len(contours)):
            contour_area[ii] = cv.contourArea(contours[ii])

            # enclosing rectangle area
            [[rectangle_min_x, rectangle_min_y]] = np.min(contours[ii], axis=0)
            [[rectangle_max_x, rectangle_max_y]] = np.max(contours[ii], axis=0)
            rectangle_area[ii] = (rectangle_max_y - rectangle_min_y) * (rectangle_max_x - rectangle_min_x)

        max_contour = np.max(contour_area)
        max_contour_ind = np.argmax(contour_area)
        if (otsu_method == OTSU_METHOD.OTSU3_FLEXIBLE_THRESH) and not take_upper_otsu3_thresh:
            fill_factor_largest_contour = contour_area[max_contour_ind] / rectangle_area[max_contour_ind]
            take_upper_otsu3_thresh = fill_factor_largest_contour > 0.97 #contour is a perfect rectangle
            if take_upper_otsu3_thresh:  # median seg hue is not purple/red or saturation is very low (white)
                # take upper threshold
                thresh = otsu3(image_array)
                _, seg_map = cv.threshold(image_array, thresh[1], 255, cv.THRESH_BINARY)

        small_contours_bool = (contour_area < size_thresh) & (contour_area < max_contour * 0.02)

    if otsu_method == OTSU_METHOD.OTSU_REGULAR and not is_IHC_slide: # do not filter small parts filtering for lung cancer
        small_contours = [contours[ii] for ii in range(len(contours)) if small_contours_bool[ii]==True]
        seg_map_filt = cv.drawContours(seg_map_filt, small_contours, -1, (0, 0, 255), thickness=cv.FILLED) #delete the small contours

    # check contour color only for large contours
    if not is_IHC_slide:
        hsv_image = np.array(image.convert('HSV'))
        rgb_image = np.array(image)
        large_contour_ind = np.where(small_contours_bool == False)[0]
        white_mask = np.zeros(seg_map.shape, np.uint8)
        white_mask[np.any(rgb_image < 240, axis=2)] = 255
        gray_contours_bool = np.zeros(len(contours), dtype=bool)
        for ii in large_contour_ind:
            # get contour mean color
            mask = np.zeros(seg_map.shape, np.uint8)
            cv.drawContours(mask, [contours[ii]], -1, 255, thickness=cv.FILLED)
            contour_color, _ = cv.meanStdDev(rgb_image, mask=mask)
            contour_std = np.std(contour_color)
            if contour_std < 5:
                hist_mask = cv.bitwise_and(white_mask, mask)
                mean_col, _ = cv.meanStdDev(hsv_image, mask=hist_mask)
                mean_hue = mean_col[0]
                if mean_hue < 100:
                    gray_contours_bool[ii] = True

        gray_contours = [contours[ii] for ii in large_contour_ind if gray_contours_bool[ii] == True]
        # delete the small contours
        seg_map_filt = cv.drawContours(seg_map_filt, gray_contours, -1, (0, 0, 255), thickness=cv.FILLED)

    # multiply seg_map with seg_map_filt
    seg_map *= (seg_map_filt > 0)

    seg_map_PIL = Image.fromarray(seg_map)

    edge_image = cv.Canny(seg_map, 1, 254)
    # Make the edge thicker by dilating:
    kernel_dilation = np.ones((3, 3))
    edge_image = Image.fromarray(cv.dilate(edge_image, kernel_dilation, iterations=magnification * 2)).convert('RGB')

    return seg_map_PIL, edge_image


def _make_background_grid_for_image(file, meta_data_DF, ROOT_DIR, added_extension, DataSet,
                                    different_SegData_path_extension, tissue_coverage, tile_sz, desired_magnification):
    filename = '.'.join(os.path.basename(file).split('.')[:-1])
    database = meta_data_DF.loc[file, 'id']
    tissue_grid_file = os.path.join(ROOT_DIR, database, 'Grids' + added_extension,
                             filename + '--tlsz' + str(tile_sz) + '.data')
    background_grid_file = os.path.join(ROOT_DIR, database, 'Non_Tissue_Grids' + added_extension,
                             filename + '--tlsz' + str(tile_sz) + '_BackGround' + '.data')

    if os.path.isfile(os.path.join(ROOT_DIR, database, file)):
        height = int(meta_data_DF.loc[file, 'Height'])
        width = int(meta_data_DF.loc[file, 'Width'])

        objective_power = meta_data_DF.loc[file, 'Manipulated Objective Power']
        if objective_power == 'Missing Data':
            print('Grid was not computed for file {}'.format(file))
            print('objective power was not found')
            tile_nums = 0
            total_tiles = -1
            return tile_nums, total_tiles

        adjusted_tile_size_at_level_0 = int(tile_sz * (int(objective_power) / desired_magnification))
        basic_grid = [(row, col) for row in range(0, height, adjusted_tile_size_at_level_0) for col in
                      range(0, width, adjusted_tile_size_at_level_0)]
        total_tiles = len(basic_grid)

        with open(tissue_grid_file, 'rb') as filehandle:
            tissue_grid_list = pickle.load(filehandle)

        background_tiles = list(set(basic_grid) - set(tissue_grid_list))
        tile_nums = len(background_tiles)
        with open(background_grid_file, 'wb') as filehandle:
            pickle.dump(background_tiles, filehandle)

    return tile_nums, total_tiles


def make_background_grid(DataSet: str = 'TCGA',
                         ROOT_DIR: str = 'All Data',
                         tile_sz: int = 256,
                         tissue_coverage: float = 0.5,
                         desired_magnification: int = 10,
                         added_extension: str = '',
                         different_SegData_path_extension: str = '',
                         num_workers: int = 1):
    """    
    :param DataSet: Dataset to create background grid for 
    :param ROOT_DIR: Root Directory for the data
    :param tile_sz: Desired tile size at desired magnification.
    :param added_extension: extension for the slides_data.xlsx file and all created sub-directories. this is needed in
           the case where we want to create alternative grids and keep the grids already created
    :param different_SegData_path_extension: a parameter defining the modified name of the SegData directory.
    :return: 
    """""

    # Create alternative slides_data file (if needed):
    if added_extension != '':
        copy2(os.path.join(ROOT_DIR, DataSet, 'slides_data_' + DataSet + '.xlsx'), os.path.join(ROOT_DIR, DataSet, 'slides_data_' + DataSet + added_extension + '.xlsx'))

    slides_data_file = os.path.join(ROOT_DIR, DataSet, 'slides_data_' + DataSet + added_extension + '.xlsx')

    slides_meta_data_DF = pd.read_excel(slides_data_file)
    files = slides_meta_data_DF.loc[slides_meta_data_DF['id'] == DataSet]['file'].tolist()

    meta_data_DF = pd.DataFrame(files, columns=['file'])

    slides_meta_data_DF.set_index('file', inplace=True)
    meta_data_DF.set_index('file', inplace=True)
    tile_nums = []
    total_tiles = []

    # Save the background grid to file:
    if not os.path.isdir(os.path.join(ROOT_DIR, DataSet, 'Non_Tissue_Grids' + added_extension)):
        os.mkdir(os.path.join(ROOT_DIR, DataSet, 'Non_Tissue_Grids' + added_extension))

    print('Starting Non Tissue Grid production...')
    print()

    tile_nums = []
    total_tiles = []

    for file in files:
        tile_nums1, total_tiles1 = _make_background_grid_for_image(file=file,
                                                                   meta_data_DF=slides_meta_data_DF,
                                                                   ROOT_DIR=ROOT_DIR,
                                                                   added_extension=added_extension,
                                                                   DataSet=DataSet,
                                                                   different_SegData_path_extension=different_SegData_path_extension,
                                                                   tissue_coverage=tissue_coverage,
                                                                   tile_sz=tile_sz,
                                                                   desired_magnification=desired_magnification)

        tile_nums.append(tile_nums1)
        total_tiles.append(total_tiles1)

    meta_data_DF.loc[files, 'BackGround tiles - ' + str(tile_sz) + ' compatible @ X' + str(desired_magnification)] = tile_nums
    meta_data_DF.loc[files, 'Total tiles - ' + str(tile_sz) + ' compatible @ X' + str(desired_magnification)] = total_tiles

    meta_data_DF.to_excel(os.path.join(ROOT_DIR, DataSet, 'Non_Tissue_Grids' + added_extension, 'Grid_data.xlsx'))

    # Save Grids creation MetaData to file
    bacground_grid_productoin_meta_data_dict = {'Creation Date': str(date.today()),
                                                'Tissue Coverage': tissue_coverage,
                                                'Segmantation Path': os.path.join(ROOT_DIR, DataSet,
                                                                                  'SegData' + different_SegData_path_extension)
                                      }

    grid_production_DF = pd.DataFrame([bacground_grid_productoin_meta_data_dict]).transpose()
    grid_production_DF.to_excel(os.path.join(ROOT_DIR, DataSet, 'Non_Tissue_Grids' + added_extension, 'production_meta_data.xlsx'))

    print('Finished BackGround Grid production phase !')


def determine_manipulated_objective_power(DataSet, ROOT_DIR):
    slides_data_file = Dataset_Maker.dataset_utils.get_slides_data_file(ROOT_DIR, DataSet)
    slides_data_df = Dataset_Maker.dataset_utils.open_excel_file(slides_data_file)
    slides_data_df['Manipulated Objective Power'] = slides_data_df['Objective Power']
    for i_row, _ in slides_data_df.iterrows():
        slide_name = slides_data_df.at[i_row, 'file']
        filetype = os.path.splitext(slide_name)[-1]
        if filetype == '.mrxs':
            slides_data_df.at[i_row, 'Manipulated Objective Power'] *= 2
    slides_data_df.to_excel(slides_data_file)
    print('adjusted "Manipulated Objective Power" column in slides_data file')