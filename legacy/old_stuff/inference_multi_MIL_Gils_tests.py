import Omer_files_suspected_as_unnecessary.omer_utils
import utils
import datasets
from torch.utils.data import DataLoader
import torch
import numpy as np
from sklearn.metrics import roc_curve, auc
import os
import sys
import argparse
from tqdm import tqdm

parser = argparse.ArgumentParser(description='WSI_MIL Slide inference')
parser.add_argument('-ex', '--experiment', type=int, default=[303], help='Continue train of this experiment')
parser.add_argument('-fe', '--from_epoch', type=int, default=[1000], help='Use this epoch model for inference')
parser.add_argument('-nt', '--num_tiles', type=int, default=16, help='Number of tiles to use')
parser.add_argument('-ds', '--dataset', type=str, default='TCGA', help='DataSet to use')
parser.add_argument('-f', '--folds', type=list, default=[2], help=' folds to infer')
args = parser.parse_args()

args.folds = list(map(int, args.folds))

if sys.platform == 'darwin':
    args.experiment = [1, 1]
    args.from_epoch = [1035, 1040]

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

# Load saved model:
print('Loading pre-saved models:')
models = []


for counter in range(len(args.from_epoch)):
    epoch = args.from_epoch[counter]
    experiment = args.experiment[counter] if different_experiments else args.experiment[0]

    print('  Exp. {} and Epoch {}'.format(experiment, epoch))
    # Basic meta data will be taken from the first model (ONLY if all inferences are done from the same experiment)
    if counter == 0:
        output_dir, _, _, TILE_SIZE, _, _, _, _, args.target, _, model_name, desired_magnification = utils.run_data(experiment=experiment)
        if different_experiments:
            Output_Dirs.append(output_dir)
        fix_data_path = True
    elif counter > 0 and different_experiments:
        output_dir, _, _, _, _, _, _, _, target, _, model_name, desired_magnification = utils.run_data(experiment=experiment)
        Output_Dirs.append(output_dir)
        fix_data_path = True

    if fix_data_path:
        # we need to make some root modifications according to the computer we're running at.
        if sys.platform == 'linux':
            data_path = ''
        elif sys.platform == 'win32':
            output_dir = output_dir.replace(r'/', '\\')
            data_path = os.getcwd()

        fix_data_path = False

        # Verifying that the target receptor is not changed:
        if counter > 1 and args.target != target:
            raise Exception("Target Receptor is changed between models - DataSet cannot support this action")

    # Tile size definition:
    if sys.platform == 'darwin':
        TILE_SIZE = 128

    # loading basic model type
    model = eval(model_name)
    # loading model parameters from the specific epoch
    model_data_loaded = torch.load(os.path.join(data_path, output_dir, 'Model_CheckPoints',
                                                'model_data_Epoch_' + str(epoch) + '.pt'), map_location='cpu')
    model.load_state_dict(model_data_loaded['model_state_dict'])
    model.eval()
    model.infer = True
    model.features_part = True
    models.append(model)

inf_dset = datasets.Infer_Dataset(DataSet=args.dataset,
                                  tile_size=TILE_SIZE,
                                  tiles_per_iter=20,
                                  target_kind=args.target,
                                  folds=args.folds,
                                  num_tiles=args.num_tiles,
                                  desired_slide_magnification=desired_magnification)

inf_loader = DataLoader(inf_dset, batch_size=1, shuffle=False, num_workers=0, pin_memory=True)

new_slide = True

NUM_MODELS = len(models)
NUM_SLIDES = len(inf_dset.valid_slide_indices)

TILES_IN_BAG = [1, 2, 4, 8, 16]

scores_class_1_per_bag = np.zeros((NUM_SLIDES, len(TILES_IN_BAG), NUM_MODELS))

all_targets = []
all_scores_for_class_1, all_labels = np.zeros((NUM_SLIDES, NUM_MODELS)), np.zeros((NUM_SLIDES, NUM_MODELS))
#all_weights_after_sftmx = np.zeros((NUM_SLIDES, args.num_tiles, NUM_MODELS))
tile_scores = np.empty((NUM_SLIDES, NUM_MODELS, args.num_tiles))
tile_scores[:] = np.nan
all_patient_barcodes = []
slide_names = []
slide_num = 0

# The following 2 lines initialize variables to compute AUC for train dataset.
total_pos, total_neg = 0, 0
correct_pos, correct_neg = [0] * NUM_MODELS, [0] * NUM_MODELS

with torch.no_grad():
    for batch_idx, (data, target, time_list, last_batch, num_tiles, slide_name, patient_barcode) in enumerate(tqdm(inf_loader)):
        if new_slide:
            all_patient_barcodes.append(patient_barcode[0])
            slide_names.append(slide_name)
            scores_0, scores_1 = [np.zeros(0)] * NUM_MODELS, [np.zeros(0)] * NUM_MODELS
            target_current = target
            slide_batch_num = 0
            new_slide = False

            all_features = np.zeros([num_tiles, model.M, NUM_MODELS], dtype='float32')
            all_weights_before_sftmx = np.zeros([1, num_tiles, NUM_MODELS], dtype='float32')

        data, target = data.to(DEVICE), target.to(DEVICE)

        for model_num, model in enumerate(models):
            model.to(DEVICE)
            features, weights_before_sftmx = model(data)

            all_features[slide_batch_num * inf_dset.tiles_per_iter: (slide_batch_num + 1) * inf_dset.tiles_per_iter, :, model_num] = features.detach().cpu().numpy()
            all_weights_before_sftmx[:, slide_batch_num * inf_dset.tiles_per_iter: (slide_batch_num + 1) * inf_dset.tiles_per_iter, model_num] = weights_before_sftmx.detach().cpu().numpy()

        slide_batch_num += 1

        if last_batch:
            # Save tile features and last models layer to file:
            new_slide = True

            all_targets.append(target.item())

            if target.item() == 1:
                total_pos += 1
            elif target.item() == 0:
                total_neg += 1

            all_features, all_weights_before_sftmx = torch.from_numpy(all_features).to(DEVICE), torch.from_numpy(all_weights_before_sftmx).to(DEVICE)

            for tib_idx, tib in enumerate(TILES_IN_BAG):
                NUM_BAGS = args.num_tiles // tib

                for model_num, model in enumerate(models):
                    model.features_part = False
                    model.to(DEVICE)

                    for bag_num in range(NUM_BAGS):
                        bag_features = all_features[tib * bag_num : tib * (bag_num + 1), :, model_num]
                        bag_weights = all_weights_before_sftmx[:, tib * bag_num : tib * (bag_num + 1), model_num]

                        bag_score, bag_weights = model(data, bag_features, bag_weights)
                        if bag_num == 0:
                            scores = bag_score / NUM_BAGS
                        else:
                            scores += bag_score / NUM_BAGS

                    #scores, weights = model(data, all_features[:, :, model_num], all_weights_before_sftmx[:, :, model_num])

                    model.features_part = True

                    scores = torch.nn.functional.softmax(scores, dim=1)
                    _, predicted = scores.max(1)

                    scores_0[model_num] = np.concatenate((scores_0[model_num], scores[:, 0].cpu().detach().numpy()))
                    scores_1[model_num] = np.concatenate((scores_1[model_num], scores[:, 1].cpu().detach().numpy()))

                    if target.item() == 1 and predicted.item() == 1:
                        correct_pos[model_num] += 1
                    elif target.item() == 0 and predicted.item() == 0:
                        correct_neg[model_num] += 1

                    #all_scores_for_class_1[slide_num, model_num] = scores_1[model_num]
                    #all_labels[slide_num, model_num] = predicted.item()

                    scores_class_1_per_bag[slide_num, tib_idx, model_num] = scores[:, 1].cpu().detach().numpy()

            slide_num += 1

# Computing performance data for all models (over all slides scores data):
'''original_stdout = sys.stdout
filename = os.path.join(data_path, output_dir, 'Inference_For_Gil', 'output.txt')
with open(filename, 'w') as f:
    sys.stdout = f  '''
for tib_idx, tib in enumerate(TILES_IN_BAG):
    for model_num in range(NUM_MODELS):
        if different_experiments:
            output_dir = Output_Dirs[model_num]

        # We'll now gather the data for computing performance per patient:
        all_targets_per_patient, all_scores_for_class_1_per_patient = Omer_files_suspected_as_unnecessary.omer_utils.gather_per_patient_data(all_targets, scores_class_1_per_bag[:, tib_idx, model_num], all_patient_barcodes)

        fpr_patient, tpr_patient, _ = roc_curve(all_targets_per_patient, all_scores_for_class_1_per_patient)
        roc_auc_patient = auc(fpr_patient, tpr_patient)

        fpr, tpr, _ = roc_curve(all_targets, scores_class_1_per_bag[:, tib_idx, model_num])
        roc_auc = auc(fpr, tpr)

        experiment = args.experiment[model_num] if different_experiments else args.experiment[0]

        if not os.path.isdir(os.path.join(data_path, output_dir, 'Inference_For_Gil')):
            os.mkdir(os.path.join(data_path, output_dir, 'Inference_For_Gil'))

        print('For model from Experiment {} and Epoch {}: {} / {} correct classifications'
              .format(experiment,
                      args.from_epoch[model_num],
                      int(len(all_labels[:, model_num]) - np.abs(np.array(all_targets) - np.array(all_labels[:, model_num])).sum()),
                      len(all_labels[:, model_num])))
        print('{} Tiles per bag, AUC per Slide = {} '.format(tib, roc_auc))
        print('{} Tiles per bag, AUC per Patient = {} '.format(tib, roc_auc_patient))

'''sys.stdout = original_stdout'''
print('Done !')
