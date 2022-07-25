import utils
from torch.utils.data import DataLoader
import torch
import datasets
import numpy as np
from sklearn.metrics import roc_curve
import os
import sys, platform
import argparse
from tqdm import tqdm
import pickle
from collections import OrderedDict
from Nets import resnet_v2, PreActResNets
import send_gmail
import logging

parser = argparse.ArgumentParser(description='WSI_REG Slide inference')
parser.add_argument('-ex', '--experiment', nargs='+', type=int, default=[10607], help='Use models from this experiment')
parser.add_argument('-fe', '--from_epoch', nargs='+', type=int, default=[960], help='Use this epoch models for inference')
parser.add_argument('-nt', '--num_tiles', type=int, default=10, help='Number of tiles to use')
parser.add_argument('-ds', '--dataset', type=str, default='ABCTB', help='DataSet to use')
parser.add_argument('-f', '--folds', type=list, nargs="+", default=1, help=' folds to infer')
parser.add_argument('--mag', type=int, default=10, help='desired magnification of patches')
parser.add_argument('-mp', '--model_path', type=str, default='', help='fixed path of rons model')  # r'/home/rschley/Pathnet/results/fold_1_ER_large/checkpoint/ckpt_epoch_1467.pth'
parser.add_argument('--save_features', action='store_true', help='save features')
parser.add_argument('-d', dest='dx', action='store_true', help='Use ONLY DX cut slides')  # override run_data
parser.add_argument('--resume', type=int, default=0, help='resume a failed feature extraction')
parser.add_argument('--patch_dir', type=str, default='', help='patch locations directory, for use with predecided patches')
parser.add_argument('-sd', '--subdir', type=str, default='', help='output sub-dir')
args = parser.parse_args()

args.folds = list(map(int, args.folds[0]))

utils.start_log(args)

# If args.experiment contains 1 number than all epochs are from the same experiments,
# BUT if it is bigger than 1 than all the length of args.experiment should be equal to args.from_epoch
if len(args.experiment) > 1:
    if len(args.experiment) != len(args.from_epoch):
        raise Exception("number of from_epoch(-fe) should be equal to number of experiment(-ex)")
    else:
        different_experiments = True
        Output_Dirs = []
else:
    different_experiments = False

DEVICE = utils.device_gpu_cpu()
data_path = ''

logging.info('Loading pre-saved models:')
models = []
dx = False

# decide which epochs to save features from - if model_path is used, take it.
# else, if there only one epoch, take it. otherwise take epoch 1000
if args.save_features:
    if args.model_path != '':
        feature_epoch_ind = len(args.from_epoch)
    elif len(args.from_epoch) > 1:
        if sys.platform == 'win32':
            feature_epoch_ind = (args.from_epoch).index(16)
        else:
            try:
                feature_epoch_ind = (args.from_epoch).index(1000)
            except ValueError:
                feature_epoch_ind = (args.from_epoch).index(2000) #If 1000 is not on the list, take epoch 2000
    elif len(args.from_epoch) == 1:
        feature_epoch_ind = 0

for counter in range(len(args.from_epoch)):
    epoch = args.from_epoch[counter]
    experiment = args.experiment[counter] if different_experiments else args.experiment[0]

    logging.info('  Exp. {} and Epoch {}'.format(experiment, epoch))
    # Basic meta data will be taken from the first model (ONLY if all inferences are done from the same experiment)
    if counter == 0:
        run_data_output = utils.run_data(experiment=experiment)
        output_dir, TILE_SIZE, dx, args.target, model_name, args.mag =\
            run_data_output['Location'], run_data_output['Tile Size'], run_data_output['DX'], run_data_output['Receptor'],\
            run_data_output['Model Name'], run_data_output['Desired Slide Magnification']
        if different_experiments:
            Output_Dirs.append(output_dir)
        fix_data_path = True
    elif counter > 0 and different_experiments:
        run_data_output = utils.run_data(experiment=experiment)
        output_dir, dx, target, model_name, args.mag =\
            run_data_output['Location'], run_data_output['DX'], run_data_output['Receptor'],\
            run_data_output['Model Name'], run_data_output['Desired Slide Magnification']
        Output_Dirs.append(output_dir)
        fix_data_path = True

    # fix target:
    if args.target in ['Features_Survival_Time_Cox', 'Features_Survival_Time_L2', 'Survival_Time_Cox']:
        args.target = 'survival'
        survival_kind = 'Time'
        target_for_file_name = args.target + '_' + survival_kind
    else:
        survival_kind = 'Binary'

    if fix_data_path:
        # we need to make some root modifications according to the computer we're running at.
        if sys.platform == 'linux':
            data_path = ''

        elif sys.platform == 'win32':
            output_dir = output_dir.replace(r'/', '\\')
            data_path = os.getcwd()

        elif sys.platform == 'darwin':
            output_dir = '/'.join(output_dir.split('/')[4:])
            data_path = os.getcwd()

        fix_data_path = False

        # Verifying that the target receptor is not changed:
        if counter > 1 and args.target != target:
            raise Exception("Target Receptor is changed between models - DataSet cannot support this action")

    if len(args.target.split('+')) > 1:
        multi_target = True
        target_list = args.target.split('+')
        N_targets = len(target_list)
        #target0, target1 = args.target.split('+')
        #if counter == 0 and model_name[-15:] != '(num_classes=4)':
        #    model_name = model_name[:-2] + '(num_classes=4)' #manually add num_classes since the arguments are not saved in run_data
    else:
        multi_target = False

    # loading basic model type
    model = eval(model_name)
    # loading model parameters from the specific epoch
    model_data_loaded = torch.load(os.path.join(data_path, output_dir, 'Model_CheckPoints',
                                                'model_data_Epoch_' + str(epoch) + '.pt'), map_location='cpu')
    # Making sure that the size of the linear layer of the loaded model, fits the basic model.
    model.linear = torch.nn.Linear(in_features=model_data_loaded['model_state_dict']['linear.weight'].size(1),
                                   out_features=model_data_loaded['model_state_dict']['linear.weight'].size(0))
    model.load_state_dict(model_data_loaded['model_state_dict'])
    model.eval()
    models.append(model)

#get number of classes based on the first model
try:
    N_classes = models[0].linear.out_features #for resnets and such
except:
    N_classes = models[0]._final_1x1_conv.out_channels #for StereoSphereRes

# override run_data dx if args.dx is true
if args.dx:
    dx = args.dx

TILE_SIZE = 128
tiles_per_iter = 20
if sys.platform == 'linux':
    TILE_SIZE = 256
    tiles_per_iter = 150
    if platform.node() in ['gipdeep4', 'gipdeep5', 'gipdeep6']:
        tiles_per_iter = 100
elif sys.platform == 'win32':
    TILE_SIZE = 256

if (args.dataset[:3] == 'TMA') and (args.mag == 7):
    TILE_SIZE = 512

# support ron's model as well
if args.model_path != '':
    if os.path.exists(args.model_path):
        args.from_epoch.append('rons_model')
        model = resnet_v2.PreActResNet50()
        model_data_loaded = torch.load(os.path.join(args.model_path), map_location='cpu')

        try:
            model.load_state_dict(model_data_loaded['net'])
        except:
            state_dict = model_data_loaded['net']
            new_state_dict = OrderedDict()
            for k, v in state_dict.items():
                name = k[7:]  # remove 'module.' of dataparallel
                new_state_dict[name] = v
            model.load_state_dict(new_state_dict)
    else:
        # use pretrained model
        args.from_epoch.append(args.model_path.split('.')[-1])
        model = eval(args.model_path)
        model.fc = torch.nn.Identity()
        tiles_per_iter = 100
    model.eval()
    models.append(model)

if args.save_features:
    logging.info('features will be taken from model ', str(args.from_epoch[feature_epoch_ind]))

slide_num = args.resume

inf_dset = datasets.Infer_Dataset(DataSet=args.dataset,
                                  tile_size=TILE_SIZE,
                                  tiles_per_iter=tiles_per_iter,
                                  target_kind=args.target,
                                  folds=args.folds,
                                  num_tiles=args.num_tiles,
                                  desired_slide_magnification=args.mag,
                                  dx=dx,
                                  resume_slide=slide_num,
                                  patch_dir=args.patch_dir)

inf_loader = DataLoader(inf_dset, batch_size=1, shuffle=False, num_workers=0, pin_memory=True)

new_slide = True

NUM_MODELS = len(models)
NUM_SLIDES = len(inf_dset.image_file_names)
NUM_SLIDES_SAVE = 50
logging.info('NUM_SLIDES: {}'.format(NUM_SLIDES))

if multi_target:
    all_targets = np.zeros((N_targets, 0))
    total_pos, total_neg = np.zeros(N_targets), np.zeros(N_targets)
    patch_scores = np.empty((NUM_SLIDES, NUM_MODELS, args.num_tiles, N_targets))
    all_scores, all_labels = np.zeros((NUM_SLIDES, NUM_MODELS, N_targets)), np.zeros((NUM_SLIDES, NUM_MODELS, N_targets))
    correct_pos = np.zeros((NUM_MODELS, N_targets))
    correct_neg = np.zeros((NUM_MODELS, N_targets))
else:
    all_targets = []
    total_pos, total_neg = 0, 0
    all_labels = np.zeros((NUM_SLIDES, NUM_MODELS))
    if N_classes == 2:
        all_scores = np.zeros((NUM_SLIDES, NUM_MODELS))
        patch_scores = np.empty((NUM_SLIDES, NUM_MODELS, args.num_tiles))
    else:
        all_scores = np.zeros((NUM_SLIDES, NUM_MODELS, N_classes))
        patch_scores = np.empty((NUM_SLIDES, NUM_MODELS, args.num_tiles, N_classes))
    correct_pos = np.zeros(NUM_MODELS)
    correct_neg = np.zeros(NUM_MODELS)

patch_locs_all = np.empty((NUM_SLIDES, args.num_tiles, 2))
if args.save_features:
    features_all = np.empty((NUM_SLIDES_SAVE, 1, args.num_tiles, 512))
    features_all[:] = np.nan
all_slide_names = np.zeros(NUM_SLIDES, dtype=object)
all_slide_datasets = np.zeros(NUM_SLIDES, dtype=object)
patch_scores[:] = np.nan
patch_locs_all[:] = np.nan

# The following 2 lines initialize variables to compute AUC for train dataset.
#correct_pos = [0 for ii in range(NUM_MODELS)]
#correct_neg = [0 for ii in range(NUM_MODELS)]


if args.resume:
    # load the inference state
    resume_file_name = os.path.join(data_path, output_dir, 'Inference', args.subdir,
                                    'Exp_' + str(args.experiment[0])
                                    + '-Folds_' + str(args.folds) + '_' + str(
                                        args.target) + '-Tiles_' + str(
                                        args.num_tiles) + '_resume_slide_num_' + str(slide_num) + '.data')
    with open(resume_file_name, 'rb') as filehandle:
        resume_data = pickle.load(filehandle)
    all_labels, all_targets, all_scores, total_pos, correct_pos, total_neg, \
    correct_neg, patch_scores, all_slide_names, all_slide_datasets, NUM_SLIDES, patch_locs_all = resume_data
else:
    resume_file_name = 0

if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference')):
    os.mkdir(os.path.join(data_path, output_dir, 'Inference'))

if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference', args.subdir)):
    os.mkdir(os.path.join(data_path, output_dir, 'Inference', args.subdir))

with torch.no_grad():
    for batch_idx, MiniBatch_Dict in enumerate(tqdm(inf_loader)):

        # Unpacking the data:
        data = MiniBatch_Dict['Data']
        target = MiniBatch_Dict['Label']
        time_list = MiniBatch_Dict['Time List']
        last_batch = MiniBatch_Dict['Is Last Batch']
        slide_file = MiniBatch_Dict['Slide Filename']
        slide_dataset = MiniBatch_Dict['Slide DataSet']
        patch_locs = MiniBatch_Dict['Patch Loc']

        if new_slide:
            n_tiles = inf_loader.dataset.num_tiles[slide_num - args.resume]

            current_slide_tile_scores = [np.zeros((n_tiles, N_classes)) for ii in range(NUM_MODELS)]
            patch_locs_1_slide = np.zeros((n_tiles, 2))
            if args.save_features:
                feature_arr = [np.zeros((n_tiles, 512))]
            target_current = target
            slide_batch_num = 0
            new_slide = False

        data = data.squeeze(0)
        data, target = data.to(DEVICE), target.to(DEVICE)

        patch_locs_1_slide[slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data),:] = np.array(patch_locs)

        for model_ind, model in enumerate(models):
            model.to(DEVICE)

            if model._get_name() == 'PreActResNet_Ron':
                scores, features = model(data)
            elif model._get_name() == 'ResNet':
                #use resnet only for features, dump scores
                features = model(data)
                scores = torch.zeros((len(data), 2))
                logging.info('Extracting features only for pretrained ResNet')
            else:
                raise IOError('Net not supported yet for feature and score extraction, implement!')

            if multi_target:
                outputs_softmax = torch.zeros_like(scores)
                for i_target in range(N_targets):
                    outputs_softmax[:, i_target * 2: i_target * 2 + 2] = torch.nn.functional.softmax(scores[:, i_target * 2: i_target * 2 + 2], dim=1)
                scores = outputs_softmax
                #scores = torch.cat((torch.nn.functional.softmax(scores[:, :2], dim=1),
                #                     torch.nn.functional.softmax(scores[:, 2:], dim=1)), dim=1)
            else:
                scores = torch.nn.functional.softmax(scores, dim=1)

            current_slide_tile_scores[model_ind][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data), :] = scores.cpu().detach().numpy()
            #scores_0[model_ind][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data)] = scores[:, 0].cpu().detach().numpy()
            #scores_1[model_ind][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data)] = scores[:, 1].cpu().detach().numpy()
            #if multi_target:
            #    scores_2[model_ind][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data)] = scores[:, 2].cpu().detach().numpy()
            #    scores_3[model_ind][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data)] = scores[:, 3].cpu().detach().numpy()

            if args.save_features:
                if model_ind == feature_epoch_ind:
                    feature_arr[0][slide_batch_num * tiles_per_iter: slide_batch_num * tiles_per_iter + len(data), :] = features.cpu().detach().numpy()

        slide_batch_num += 1

        if last_batch:
            new_slide = True

            if multi_target:
                target_squeezed = torch.transpose(torch.squeeze(target, 2), 0, 1)
                all_targets = np.hstack((all_targets, target_squeezed.cpu().numpy()))
                for i_target in range(N_targets):
                    total_pos[i_target] += torch.squeeze(target[:, i_target]).eq(1).sum().item()
                    total_neg[i_target] += torch.squeeze(target[:, i_target]).eq(0).sum().item()
                #total_pos += np.array((torch.squeeze(target[:, 0]).eq(1).sum().item(), torch.squeeze(target[:, 1]).eq(1).sum().item()))
                #total_neg += np.array((torch.squeeze(target[:, 0]).eq(0).sum().item(), torch.squeeze(target[:, 1]).eq(0).sum().item()))
            else:
                all_targets.append(target.cpu().numpy()[0][0])
                if N_classes == 2:
                    if target == 1:
                        total_pos += 1
                    else:
                        total_neg += 1

            if args.save_features:
                features_all[slide_num % NUM_SLIDES_SAVE, 0, :len(feature_arr[0])] = feature_arr[0]

            patch_locs_all[slide_num, :len(patch_locs_1_slide), :] = patch_locs_1_slide

            for model_ind in range(NUM_MODELS):
                if multi_target:
                    batch_len = len(target)
                    predicted = torch.zeros(N_targets, batch_len)
                    for i_target in range(N_targets):
                        predicted[i_target, :] = current_slide_tile_scores[model_ind][:, i_target * 2: i_target * 2 + 2].mean(0).argmax()
                        patch_scores[slide_num, model_ind, :n_tiles, i_target] = current_slide_tile_scores[model_ind][:, i_target * 2 + 1]
                        all_scores[slide_num, model_ind, i_target] = current_slide_tile_scores[model_ind][:, i_target * 2 + 1].mean()
                    #patch_scores[slide_num, model_ind, :n_tiles, 0] = current_slide_tile_scores[model_ind][:, 1]
                    #patch_scores[slide_num, model_ind, :n_tiles, 1] = current_slide_tile_scores[model_ind][:, 3]
                    #all_scores[slide_num, model_ind, 0] = current_slide_tile_scores[model_ind][:, 1].mean()
                    #all_scores[slide_num, model_ind, 1] = current_slide_tile_scores[model_ind][:, 3].mean()
                    correct_pos[model_ind] += np.squeeze((predicted == 1).cpu().numpy() & (target_squeezed.eq(1).cpu().numpy()))
                    correct_neg[model_ind] += np.squeeze((predicted == 0).cpu().numpy() & (target_squeezed.eq(0).cpu().numpy()))
                else:
                    predicted = current_slide_tile_scores[model_ind].mean(0).argmax()
                    # = np.vstack((scores_0[model_num], scores_1[model_num]))
                    #predicted = current_slide_tile_scores.mean(1).argmax()
                    #patch_scores[slide_num, model_ind, :len(scores_1[model_ind])] = scores_1[model_ind]
                    if N_classes == 2:
                        patch_scores[slide_num, model_ind, :n_tiles] = current_slide_tile_scores[model_ind][:, 1]
                        all_scores[slide_num, model_ind] = current_slide_tile_scores[model_ind][:, 1].mean()
                        if target == 1 and predicted == 1:
                            correct_pos[model_ind] += 1
                        elif target == 0 and predicted == 0:
                            correct_neg[model_ind] += 1
                    else: #multiclass
                        patch_scores[slide_num, model_ind, :n_tiles, :] = current_slide_tile_scores[model_ind]
                        all_scores[slide_num, model_ind, :] = current_slide_tile_scores[model_ind].mean(0)

                all_labels[slide_num, model_ind] = np.squeeze(predicted)
                all_slide_names[slide_num] = slide_file[0]
                all_slide_datasets[slide_num] = slide_dataset[0]

            slide_num += 1

            # save features every NUM_SLIDES_SAVE slides
            if slide_num % NUM_SLIDES_SAVE == 0:
                #save the inference state
                prev_resume_file_name = resume_file_name
                resume_file_name = os.path.join(data_path, output_dir, 'Inference', args.subdir,
                                                 'Exp_' + str(args.experiment[0])
                                                 + '-Folds_' + str(args.folds) + '_' + str(
                                                     args.target) + '-Tiles_' + str(
                                                     args.num_tiles) + '_resume_slide_num_' + str(slide_num) + '.data')
                resume_data = [all_labels, all_targets, all_scores,
                                  total_pos, correct_pos, total_neg, correct_neg,
                                  patch_scores, all_slide_names, all_slide_datasets, NUM_SLIDES, patch_locs_all]

                with open(resume_file_name, 'wb') as filehandle:
                    pickle.dump(resume_data, filehandle)
                #delete previous resume file
                if os.path.isfile(prev_resume_file_name):
                    os.remove(prev_resume_file_name)

                #save features
                if args.save_features:
                    feature_file_name = os.path.join(data_path, output_dir, 'Inference', args.subdir,
                                                     'Model_Epoch_' + str(args.from_epoch[feature_epoch_ind])
                                                     + '-Folds_' + str(args.folds) + '_' + str(
                                                         args.target) + '-Tiles_' + str(args.num_tiles) + '_features_slides_' + str(slide_num) + '.data')
                    inference_data = [all_labels[slide_num-NUM_SLIDES_SAVE:slide_num, feature_epoch_ind],
                                      all_targets[slide_num-NUM_SLIDES_SAVE:slide_num],
                                      all_scores[slide_num-NUM_SLIDES_SAVE:slide_num, feature_epoch_ind],
                                      np.squeeze(patch_scores[slide_num-NUM_SLIDES_SAVE:slide_num, feature_epoch_ind, :]),
                                      all_slide_names[slide_num-NUM_SLIDES_SAVE:slide_num],
                                      features_all,
                                      all_slide_datasets[slide_num-NUM_SLIDES_SAVE:slide_num],
                                      patch_locs_all[slide_num-NUM_SLIDES_SAVE:slide_num]]
                    with open(feature_file_name, 'wb') as filehandle:
                        pickle.dump(inference_data, filehandle)
                    logging.info('saved output for ', str(slide_num), ' slides')
                    features_all = np.empty((NUM_SLIDES_SAVE, 1, args.num_tiles, 512))
                    features_all[:] = np.nan

#save features for last slides
if args.save_features and slide_num % NUM_SLIDES_SAVE != 0:
    #for model_num in range(NUM_MODELS):
    feature_file_name = os.path.join(data_path, output_dir, 'Inference', args.subdir,
                                     'Model_Epoch_' + str(args.from_epoch[feature_epoch_ind])
                                     + '-Folds_' + str(args.folds) + '_' + str(
                                         args.target) + '-Tiles_' + str(args.num_tiles) + '_features_slides_last.data')
    last_save = slide_num // NUM_SLIDES_SAVE * NUM_SLIDES_SAVE
    inference_data = [all_labels[last_save:slide_num, feature_epoch_ind],
                      all_targets[last_save:slide_num],
                      all_scores[last_save:slide_num, feature_epoch_ind],
                      np.squeeze(patch_scores[last_save:slide_num, feature_epoch_ind, :]),
                      all_slide_names[last_save:slide_num],
                      features_all[:slide_num-last_save],
                      all_slide_datasets[last_save:slide_num],
                      patch_locs_all[last_save:slide_num]]
    with open(feature_file_name, 'wb') as filehandle:
        pickle.dump(inference_data, filehandle)
    logging.info('saved output for ', str(slide_num), ' slides')

for model_num in range(NUM_MODELS):
    if different_experiments:
        output_dir = Output_Dirs[model_num]

    # remove targets = -1 from auc calculation
    try:
        if multi_target:
            fpr, tpr = 0, 0  # calculate in open_inference
        else:
            scores_arr = all_scores[:, model_num]
            targets_arr = np.array(all_targets)
            scores_arr = scores_arr[targets_arr >= 0]
            targets_arr = targets_arr[targets_arr >= 0]
            fpr, tpr, _ = roc_curve(targets_arr, scores_arr)
    except ValueError:
        fpr, tpr = 0, 0  # if all labels are unknown

    # Save roc_curve to file:
    file_name = os.path.join(data_path, output_dir, 'Inference', args.subdir, 'Model_Epoch_' + str(args.from_epoch[model_num])
                             + '-Folds_' + str(args.folds) + '_' + str(args.target) + '-Tiles_' + str(args.num_tiles) + '.data')
    inference_data = [fpr, tpr, all_labels[:, model_num], all_targets, all_scores[:, model_num],
                      total_pos, correct_pos[model_num], total_neg, correct_neg[model_num], NUM_SLIDES,
                      np.squeeze(patch_scores[:, model_num, :]), all_slide_names, all_slide_datasets,
                      np.squeeze(patch_locs_all)]

    with open(file_name, 'wb') as filehandle:
        pickle.dump(inference_data, filehandle)

    experiment = args.experiment[model_num] if different_experiments else args.experiment[0]
    if not multi_target:
        logging.info('For model from Experiment {} and Epoch {}: {} / {} correct classifications'
              .format(experiment,
                      args.from_epoch[model_num],
                      int(len(all_labels[:, model_num]) - np.abs(np.array(all_targets) - np.array(all_labels[:, model_num])).sum()),
                      len(all_labels[:, model_num])))
logging.info('Done!')

#delete last resume file
if os.path.isfile(resume_file_name):
    os.remove(resume_file_name)

send_gmail.send_gmail(experiment, send_gmail.Mode.INFERENCE)
