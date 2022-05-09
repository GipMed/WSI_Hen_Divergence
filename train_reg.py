import utils
import datasets
from torch.utils.data import DataLoader
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch
import torch.optim as optim
from tqdm import tqdm
import time
from torch.utils.tensorboard import SummaryWriter
import argparse
import os
from sklearn.metrics import roc_curve, auc, roc_auc_score
import numpy as np
import sys
import pandas as pd
from sklearn.utils import resample
import smtplib, ssl
import psutil
import nets, PreActResNets, resnet_v2
from sphere_res import StereoSphereRes
from datetime import datetime
from Cox_Loss import Cox_loss
import re
import matplotlib.pyplot as plt
import logging

utils.send_run_data_via_mail()

DEFAULT_BATCH_SIZE = 18
parser = argparse.ArgumentParser(description='WSI_REG Training of PathNet Project')
parser.add_argument('-tf', '--test_fold', default=1, type=int, help='fold to be as TEST FOLD')
parser.add_argument('-e', '--epochs', default=1001, type=int, help='Epochs to run')
parser.add_argument('-ex', '--experiment', type=int, default=0, help='Continue train of this experiment')
parser.add_argument('-fe', '--from_epoch', type=int, default=0, help='Continue train from epoch')
parser.add_argument('-d', dest='dx', action='store_true', help='Use ONLY DX cut slides')
parser.add_argument('-ds', '--dataset', type=str, default='ABCTB', help='DataSet to use')
parser.add_argument('-time', dest='time', action='store_true', help='save train timing data ?')
parser.add_argument('-tar', '--target', default='Survival_Time', type=str, help='label: Her2/ER/PR/EGFR/PDL1')
#parser.add_argument('-tar', '--target', action='append', default='Survival_Time', type=str, help='label: Her2/ER/PR/EGFR/PDL1') #RanS 8.12.21
parser.add_argument('--n_patches_test', default=1, type=int, help='# of patches at test time')
parser.add_argument('--n_patches_train', default=10, type=int, help='# of patches at train time')
parser.add_argument('--lr', default=1e-5, type=float, help='learning rate')
parser.add_argument('--weight_decay', default=5e-5, type=float, help='L2 penalty')
parser.add_argument('-balsam', '--balanced_sampling', dest='balanced_sampling', action='store_true', help='balanced_sampling')
parser.add_argument('--transform_type', default='rvf', type=str, help='none / flip / wcfrs (weak color+flip+rotate+scale)')
parser.add_argument('--batch_size', default=DEFAULT_BATCH_SIZE, type=int, help='size of batch')
parser.add_argument('--model', default='PreActResNets.PreActResNet50_Ron()', type=str, help='net to use')
#parser.add_argument('--model', default='nets.ResNet50(pretrained=True)', type=str, help='net to use')
parser.add_argument('--bootstrap', action='store_true', help='use bootstrap to estimate test AUC error')
parser.add_argument('--eval_rate', type=int, default=5, help='Evaluate validation set every # epochs')
parser.add_argument('--c_param', default=0.1, type=float, help='color jitter parameter')
parser.add_argument('-im', dest='images', action='store_true', help='save data images?')
parser.add_argument('--mag', type=int, default=10, help='desired magnification of patches')
parser.add_argument('--loan', action='store_true', help='Localized Annotation for strongly supervised training') #RanS 17.6.21
parser.add_argument('--er_eq_pr', action='store_true', help='while training, take only er=pr examples') #RanS 27.6.21
parser.add_argument('--focal', action='store_true', help='use focal loss with gamma=2') #RanS 18.7.21
parser.add_argument('--slide_per_block', action='store_true', help='for carmel, take only one slide per block') #RanS 17.8.21
parser.add_argument('-baldat', '--balanced_dataset', dest='balanced_dataset', action='store_true', help='take same # of positive and negative patients from each dataset')  # RanS 5.9.21
parser.add_argument('--RAM_saver', action='store_true', help='use only a quarter of the slides + reshuffle every 100 epochs') #RanS 3.11.21
parser.add_argument('-tl', '--transfer_learning', default='', type=str, help='use model trained on another experiment') #RanS 17.11.21

args = parser.parse_args()

EPS = 1e-7

def start_log(args):
    logfile = os.path.join(args.output_dir, 'log.txt')
    os.makedirs(args.output_dir, exist_ok=True)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    logging.basicConfig(format='%(message)s',
                        level=logging.INFO,
                        handlers=[stream_handler,
                                  logging.FileHandler(filename=logfile)])
    logging.info('*** START ARGS ***')
    for k, v in vars(args).items():
        logging.info('{}: {}'.format(k, v))
    logging.info('*** END ARGS ***')


def train(model: nn.Module, dloader_train: DataLoader, dloader_test: DataLoader, DEVICE, optimizer, print_timing: bool=False):
    """
    This function trains the model
    :return:
    """
    if not os.path.isdir(os.path.join(args.output_dir, 'Model_CheckPoints')):
        os.mkdir(os.path.join(args.output_dir, 'Model_CheckPoints'))
    writer_folder = os.path.join(args.output_dir, 'writer')
    all_writer = SummaryWriter(os.path.join(writer_folder, 'all'))
    test_auc_list = []

    if from_epoch == 0:
        all_writer.add_text('Experiment No.', str(experiment))
        all_writer.add_text('Train type', 'Regular')
        all_writer.add_text('Model type', str(type(model)))
        all_writer.add_text('Data type', dloader_train.dataset.DataSet)
        all_writer.add_text('Train Folds', str(dloader_train.dataset.folds).strip('[]'))
        all_writer.add_text('Test Folds', str(dloader_test.dataset.folds).strip('[]'))
        all_writer.add_text('Transformations', str(dloader_train.dataset.transform))
        all_writer.add_text('Receptor Type', str(dloader_train.dataset.target_kind))

    if print_timing:
        time_writer = SummaryWriter(os.path.join(writer_folder, 'time'))

    print('Start Training...')
    previous_epoch_loss = 1e5

    for e in range(from_epoch, epoch):
        time_epoch_start = time.time()
        if args.target == 'Survival_Time':
            all_targets, all_outputs, all_censored, all_cont_targets = [], [], [], []

        if multi_target:
            true_targets_train, scores_train = np.zeros((N_targets, 0)), np.zeros((N_targets, 0))
            correct_pos, correct_neg = np.zeros(N_targets), np.zeros(N_targets)
            total_pos_train, total_neg_train = np.zeros(N_targets), np.zeros(N_targets)
            correct_labeling = np.zeros(N_targets)
        else:
            if N_classes == 2:
                scores_train = np.zeros(0)
            else:
                scores_train = np.zeros((N_classes, 0))
            true_targets_train = np.zeros(0)
            correct_pos, correct_neg = 0, 0
            total_pos_train, total_neg_train = 0, 0
            correct_labeling = 0
        train_loss, total = 0, 0

        slide_names = []
        #print('Epoch {}:'.format(e))
        logging.info('Epoch {}:'.format(e)) #RanS 31.1.22

        # RanS 11.7.21
        process = psutil.Process(os.getpid())
        #print('RAM usage:', np.round(process.memory_info().rss/1e9), 'GB, time: ', datetime.now(), ', exp: ', str(experiment))
        logging.info('RAM usage: {} GB, time: {}, exp: {}'.format(np.round(process.memory_info().rss/1e9),
                                                                  datetime.now(),
                                                                  str(experiment)))

        model.train()
        model.to(DEVICE)

        for batch_idx, minibatch in enumerate(tqdm(dloader_train)):
            data = minibatch['Data']
            target = minibatch['Target']
            time_list = minibatch['Time List']
            f_names = minibatch['File Names']

            temp_plot = False
            if temp_plot:
                fig, ax = plt.subplots(1,3)
                for ii in range(3):
                    ax[ii].imshow(data[0, ii, :, :])

            if args.target == 'Survival_Time':
                censored = minibatch['Censored']
                target_binary = minibatch['Target Binary']
            elif args.target == 'Survival_Binary':
                censored = minibatch['Censored']
                target_cont = minibatch['Survival Time']

            train_start = time.time()
            data, target = data.to(DEVICE), target.to(DEVICE).squeeze(1)

            optimizer.zero_grad()
            if print_timing:
                time_fwd_start = time.time()

            outputs, _ = model(data)

            temp_plot = False
            if temp_plot: # RanS 24.1.22
                import matplotlib.pyplot as plt
                fig1, ax1 = plt.subplots(1,4)
                for ii in range(4):
                    #ax1[ii].imshow(torch.squeeze(data[ii, :, :, :].permute(1, 2, 0))) #normalized image looks bad (color truncation at 0)
                    q1 = ax1[ii].imshow(torch.squeeze(data[ii, 1, :, :])) #red color only
                    plt.colorbar(q1, ax=ax1[ii])
                #fig1.colorbar()

            if print_timing:
                time_fwd = time.time() - time_fwd_start

            if args.target == 'Survival_Time':
                loss = criterion(outputs, target, censored)
                outputs = torch.reshape(outputs, [outputs.size(0)])
                all_outputs.extend(outputs.detach().cpu().numpy())
            else:
                if multi_target:
                    batch_len = len(target)
                    loss_array = torch.zeros(batch_len, N_targets)
                    for i_target in range(N_targets):
                        loss_array[:, i_target] = criterion(outputs[:, i_target*2 : i_target*2 + 2], target[:, i_target].reshape(batch_len))
                    #loss_array[:, 0] = criterion(outputs[:, :2], target[:, 0].reshape(batch_len))
                    #loss_array[:, 1] = criterion(outputs[:, 2:], target[:, 1].reshape(batch_len))
                    mask = loss_array != 0
                    loss = torch.mean((loss_array * mask).sum(dim=1) / mask.sum(dim=1))
                    # outputs = torch.cat((torch.nn.functional.softmax(outputs[:, :2], dim=1), torch.nn.functional.softmax(outputs[:, 2:], dim=1)),dim=1)
                    # predicted = torch.vstack((outputs[:, :2].max(1).indices, outputs[:, 2:].max(1).indices))
                    # scores_train = np.hstack((scores_train, np.vstack((outputs[:, 1].cpu().detach().numpy(), outputs[:, 3].cpu().detach().numpy()))))
                    outputs_softmax = torch.zeros_like(outputs)
                    for i_target in range(N_targets):
                        outputs_softmax[:, i_target*2: i_target*2 + 2] = torch.nn.functional.softmax(outputs[:, i_target * 2: i_target*2 + 2], dim=1)
                    outputs = outputs_softmax
                    predicted = torch.zeros(N_targets, batch_len)
                    scores_train_batch = np.zeros((N_targets, batch_len))
                    target_squeezed = torch.transpose(torch.squeeze(target, 2), 0, 1)
                    true_targets_train = np.hstack((true_targets_train, target_squeezed.cpu().detach().numpy()))
                    for i_target in range(N_targets):
                        predicted[i_target, :] = outputs[:, i_target*2 : i_target*2 + 2].max(1).indices
                        scores_train_batch[i_target, :] = outputs[:, i_target*2+1].cpu().detach().numpy()
                        total_pos_train[i_target] += torch.squeeze(target[:, i_target]).eq(1).sum().item()
                        total_neg_train[i_target] += torch.squeeze(target[:, i_target]).eq(0).sum().item()
                        correct_labeling[i_target] += predicted[i_target, :].eq(target_squeezed[i_target, :]).sum().item()
                        correct_pos[i_target] += predicted[i_target, target_squeezed[i_target, :].eq(1)].eq(1).sum().item()
                        correct_neg[i_target] += predicted[i_target, target_squeezed[i_target, :].eq(0)].eq(0).sum().item()
                    scores_train = np.hstack((scores_train, scores_train_batch))
                else:
                    loss = criterion(outputs, target)
                    outputs = torch.nn.functional.softmax(outputs, dim=1)
                    _, predicted = outputs.max(1)
                    if N_classes == 2:
                        scores_train = np.concatenate((scores_train, outputs[:, 1].cpu().detach().numpy()))
                    else:
                        scores_train = np.hstack((scores_train, outputs.cpu().detach().numpy().transpose()))
                    true_targets_train = np.concatenate((true_targets_train, target.cpu().detach().numpy()))
                    total_pos_train += target.eq(1).sum().item()
                    total_neg_train += target.eq(0).sum().item()
                    correct_labeling += predicted.eq(target).sum().item()
                    correct_pos += predicted[target.eq(1)].eq(1).sum().item()
                    correct_neg += predicted[target.eq(0)].eq(0).sum().item()
                total += target.size(0)

            if loss != 0:
                if print_timing:
                    time_backprop_start = time.time()

                loss.backward()
                if temp_plot:
                    utils.plot_grad_flow(model.named_parameters()) #RanS 24.1.22

                optimizer.step()

                train_loss += loss.item()

                if print_timing:
                    time_backprop = time.time() - time_backprop_start

            slide_names_batch = [os.path.basename(f_name) for f_name in f_names]
            slide_names.extend(slide_names_batch)

            #all_writer.add_scalar('Loss', loss.item(), batch_idx + e * len(dloader_train))
            # RanS 28.1.21
            if DEVICE.type == 'cuda' and print_timing:
                res = nvidia_smi.nvmlDeviceGetUtilizationRates(handle)
                #print(f'gpu: {res.gpu}%, gpu-mem: {res.memory}%')
                all_writer.add_scalar('GPU/gpu', res.gpu, batch_idx + e * len(dloader_train))
                all_writer.add_scalar('GPU/gpu-mem', res.memory, batch_idx + e * len(dloader_train))
            train_time = time.time() - train_start
            if print_timing:
                time_stamp = batch_idx + e * len(dloader_train)
                time_writer.add_scalar('Time/Train (iter) [Sec]', train_time, time_stamp)
                time_writer.add_scalar('Time/Forward Pass [Sec]', time_fwd, time_stamp)
                time_writer.add_scalar('Time/Back Propagation [Sec]', time_backprop, time_stamp)
                # print('Elapsed time of one train iteration is {:.2f} s'.format(train_time))
                time_list = torch.stack(time_list, 1)
                if len(time_list[0]) == 4:
                    time_writer.add_scalar('Time/Open WSI [Sec]', time_list[:, 0].mean().item(), time_stamp)
                    time_writer.add_scalar('Time/Avg to Extract Tile [Sec]', time_list[:, 1].mean().item(), time_stamp)
                    time_writer.add_scalar('Time/Augmentation [Sec]', time_list[:, 2].mean().item(), time_stamp)
                    time_writer.add_scalar('Time/Total To Collect Data [Sec]', time_list[:, 3].mean().item(), time_stamp)
                else:
                    time_writer.add_scalar('Time/Avg to Extract Tile [Sec]', time_list[:, 0].mean().item(), time_stamp)
                    time_writer.add_scalar('Time/Augmentation [Sec]', time_list[:, 1].mean().item(), time_stamp)
                    time_writer.add_scalar('Time/Total To Collect Data [Sec]', time_list[:, 2].mean().item(), time_stamp)
        #time_epoch = (time.time() - time_epoch_start) / 60

        time_epoch = (time.time() - time_epoch_start) #sec
        if print_timing:
            time_writer.add_scalar('Time/Full Epoch [min]', time_epoch / 60, e)

        train_loss /= len(dloader_train) #normalize loss
        train_acc = 100 * correct_labeling / total
        balanced_acc_train = 100. * ((correct_pos + EPS) / (total_pos_train + EPS) + (correct_neg + EPS) / (total_neg_train + EPS)) / 2

        if multi_target:
            roc_auc_train = np.empty(N_targets)
            roc_auc_train[:] = np.float64(np.nan)
            for i_target in range(N_targets):
                if len(np.unique(true_targets_train[i_target, true_targets_train[i_target, :] >= 0])) > 1:  # more than one label
                    fpr_train, tpr_train, _ = roc_curve(true_targets_train[i_target, true_targets_train[i_target, :] >= 0], scores_train[i_target, true_targets_train[i_target, :] >= 0])
                    roc_auc_train[i_target] = auc(fpr_train, tpr_train)
                    all_writer.add_scalars('Train/Balanced Accuracy', {target_list[i_target]: balanced_acc_train[i_target]}, e)
                    all_writer.add_scalars('Train/Roc-Auc', {target_list[i_target]: roc_auc_train[i_target]}, e)
                    all_writer.add_scalars('Train/Accuracy', {target_list[i_target]: train_acc[i_target]}, e)
            '''if len(np.unique(true_targets_train[1, true_targets_train[1,:] >= 0])) > 1:  # more than one label
                fpr_train, tpr_train, _ = roc_curve(true_targets_train[1, true_targets_train[1,:] >= 0], scores_train[1, true_targets_train[1,:] >= 0])
                roc_auc_train[1] = auc(fpr_train, tpr_train)
            all_writer.add_scalars('Train/Balanced Accuracy', {target0: balanced_acc_train[0], target1: balanced_acc_train[1]}, e)
            all_writer.add_scalars('Train/Roc-Auc', {target0: roc_auc_train[0], target1: roc_auc_train[1]}, e)
            all_writer.add_scalars('Train/Accuracy', {target0: train_acc[0], target1: train_acc[1]}, e)'''
        elif N_classes > 2: #multiclass
            #calculate binary AUC scores for each threshold
            for i_thresh in range(N_classes-1):
                roc_auc_train = np.float64(np.nan)
                N_pos = np.sum(true_targets_train >= (i_thresh + 1))  # num of positive targets
                N_neg = np.sum(true_targets_train < (i_thresh + 1))  # num of negative targets
                if (N_pos > 0) and (N_neg > 0):  # more than one label
                    scores_train_thresh = np.sum(scores_train[i_thresh+1:, :], axis=0)
                    true_targets_train_thresh = true_targets_train >= (i_thresh + 1)
                    fpr_train, tpr_train, _ = roc_curve(true_targets_train_thresh, scores_train_thresh)
                    roc_auc_train = auc(fpr_train, tpr_train)
                all_writer.add_scalar('Train/Roc-Auc_thresh' + str(i_thresh), roc_auc_train, e)
            all_writer.add_scalar('Train/Balanced Accuracy', balanced_acc_train, e)
            all_writer.add_scalar('Train/Accuracy', train_acc, e)
        else:
            roc_auc_train = np.float64(np.nan)
            if len(np.unique(true_targets_train[true_targets_train >= 0])) > 1:  #more than one label
                fpr_train, tpr_train, _ = roc_curve(true_targets_train, scores_train)
                roc_auc_train = auc(fpr_train, tpr_train)
            all_writer.add_scalar('Train/Balanced Accuracy', balanced_acc_train, e)
            all_writer.add_scalar('Train/Roc-Auc', roc_auc_train, e)
            all_writer.add_scalar('Train/Accuracy', train_acc, e)
        all_writer.add_scalar('Train/Loss Per Epoch', train_loss, e)
        '''print('Finished Epoch: {}, Loss: {:.2f}, Loss Delta: {:.3f}, Train AUC per patch: {:.2f} , Time: {:.0f} m'
              .format(e,
                      train_loss,
                      previous_epoch_loss - train_loss,
                      roc_auc_train if roc_auc_train.size == 1 else roc_auc_train[0],
                      time_epoch))'''
        logging.info('Finished Epoch: {}, Loss: {:.4f}, Loss Delta: {:.3f}, Train AUC per patch: {:.2f} , Time: {:.0f} m {:.0f} s'
              .format(e,
                      train_loss,
                      previous_epoch_loss - train_loss,
                      roc_auc_train if roc_auc_train.size == 1 else roc_auc_train[0],
                      time_epoch // 60,
                      time_epoch % 60))
        previous_epoch_loss = train_loss

        # Update 'Last Epoch' at run_data.xlsx file:
        utils.run_data(experiment=experiment, epoch=e)

        # Save model to file:
        try:
            model_state_dict = model.module.state_dict()
        except AttributeError:
            model_state_dict = model.state_dict()
        torch.save({'epoch': e,
                    'model_state_dict': model_state_dict,
                    'optimizer_state_dict': optimizer.state_dict(),
                    'tile_size': TILE_SIZE,
                    'tiles_per_bag': 1},
                   os.path.join(args.output_dir, 'Model_CheckPoints', 'model_data_Last_Epoch.pt'))

        if e % args.eval_rate == 0:
            acc_test, bacc_test, roc_auc_test = check_accuracy(model, dloader_test, all_writer, DEVICE, e)

            # perform slide inference
            if (not multi_target) and (N_classes == 2):
                patch_df = pd.DataFrame({'slide': slide_names, 'scores': scores_train, 'labels': true_targets_train})
                slide_mean_score_df = patch_df.groupby('slide').mean()
                roc_auc_slide = np.nan
                if not all(slide_mean_score_df['labels'] == slide_mean_score_df['labels'][0]):  #more than one label
                    roc_auc_slide = roc_auc_score(slide_mean_score_df['labels'], slide_mean_score_df['scores'])
                all_writer.add_scalar('Train/slide AUC', roc_auc_slide, e)

                test_auc_list.append(roc_auc_test)
                if len(test_auc_list) == 5:
                    test_auc_mean = np.mean(test_auc_list)
                    test_auc_list.pop(0)
                    utils.run_data(experiment=experiment, test_mean_auc=test_auc_mean)

            # Save model to file:
            try:
                model_state_dict = model.module.state_dict()
            except AttributeError:
                model_state_dict = model.state_dict()
            torch.save({'epoch': e,
                        'model_state_dict': model_state_dict,
                        'optimizer_state_dict': optimizer.state_dict(),
                        'loss': loss.item(),
                        'acc_test': acc_test,
                        'bacc_test': bacc_test,
                        'tile_size': TILE_SIZE,
                        'tiles_per_bag': 1},
                       os.path.join(args.output_dir, 'Model_CheckPoints', 'model_data_Epoch_' + str(e) + '.pt'))
            #print('saved checkpoint to', args.output_dir) #RanS 23.6.21
            logging.info('saved checkpoint to {}'.format(args.output_dir))

        #if (args.dataset == 'TMA') and (args.mag == 7): #temp RanS 24.1.22
        #    scheduler.step()

    all_writer.close()
    if print_timing:
        time_writer.close()


def check_accuracy(model: nn.Module, data_loader: DataLoader, all_writer, DEVICE, epoch: int):
    if multi_target:
        true_pos_test, true_neg_test = np.zeros(N_targets), np.zeros(N_targets)
        total_pos_test, total_neg_test = np.zeros(N_targets), np.zeros(N_targets)
        true_labels_test, scores_test = np.zeros((N_targets, 0)), np.zeros((N_targets, 0))
        correct_labeling_test = np.zeros(N_targets)
    else:
        if N_classes == 2:
            scores_test = np.zeros(0)
        else:
            scores_test = np.zeros((N_classes, 0))
        true_pos_test, true_neg_test = 0, 0
        total_pos_test, total_neg_test = 0, 0
        true_labels_test = np.zeros(0)
        correct_labeling_test = 0
    total_test = 0
    slide_names = []

    model.eval()

    with torch.no_grad():
        #for idx, (data, targets, time_list, f_names, _) in enumerate(data_loader):
        for batch_idx, minibatch in enumerate(data_loader):
            data = minibatch['Data']
            targets = minibatch['Target']
            f_names = minibatch['File Names']
            slide_names_batch = [os.path.basename(f_name) for f_name in f_names]
            slide_names.extend(slide_names_batch)

            data, targets = data.to(device=DEVICE), targets.to(device=DEVICE).squeeze(1)
            model.to(DEVICE)

            outputs, _ = model(data)

            if multi_target:
                batch_len = len(targets)
                outputs_softmax = torch.zeros_like(outputs)
                for i_target in range(N_targets):
                    outputs_softmax[:, i_target * 2: i_target * 2 + 2] = torch.nn.functional.softmax(outputs[:, i_target * 2: i_target * 2 + 2], dim=1)
                outputs = outputs_softmax
                predicted = torch.zeros(N_targets, batch_len)

                scores_test_batch = np.zeros((N_targets, batch_len))
                target_squeezed = torch.transpose(torch.squeeze(targets, 2), 0, 1)
                for i_target in range(N_targets):
                    predicted[i_target, :] = outputs[:, i_target * 2: i_target * 2 + 2].max(1).indices
                    scores_test_batch[i_target, :] = outputs[:, i_target * 2 + 1].cpu().detach().numpy()
                    total_pos_test[i_target] += torch.squeeze(targets[:, i_target]).eq(1).sum().item()
                    total_neg_test[i_target] += torch.squeeze(targets[:, i_target]).eq(0).sum().item()
                    correct_labeling_test[i_target] += predicted[i_target, :].eq(target_squeezed[i_target, :]).sum().item()
                    true_pos_test[i_target] += predicted[i_target, target_squeezed[i_target, :].eq(1)].eq(1).sum().item()
                    true_neg_test[i_target] += predicted[i_target, target_squeezed[i_target, :].eq(0)].eq(0).sum().item()

                #outputs = torch.cat((torch.nn.functional.softmax(outputs[:, :2], dim=1), torch.nn.functional.softmax(outputs[:, 2:], dim=1)), dim=1)
                #predicted = torch.vstack((outputs[:, :2].max(1).indices, outputs[:, 2:].max(1).indices))
                #scores_test = np.hstack((scores_test, np.vstack((outputs[:, 1].cpu().detach().numpy(), outputs[:, 3].cpu().detach().numpy()))))
                scores_test = np.hstack((scores_test, scores_test_batch))
                true_labels_test = np.hstack((true_labels_test, target_squeezed.cpu().detach().numpy()))
                #correct_labeling_test += np.array((predicted[0, :].eq(target_squeezed[0, :]).sum().item(), predicted[1, :].eq(target_squeezed[1, :]).sum().item()))
                #total_pos_test += np.array((torch.squeeze(targets[:, 0]).eq(1).sum().item(), torch.squeeze(targets[:, 1]).eq(1).sum().item()))
                #total_neg_test += np.array((torch.squeeze(targets[:, 0]).eq(0).sum().item(), torch.squeeze(targets[:, 1]).eq(0).sum().item()))
                #true_pos_test += np.array((predicted[0, target_squeezed[0, :].eq(1)].eq(1).sum().item(), predicted[1, target_squeezed[1, :].eq(1)].eq(1).sum().item()))
                #true_neg_test += np.array((predicted[0, target_squeezed[0, :].eq(0)].eq(0).sum().item(), predicted[1, target_squeezed[1, :].eq(0)].eq(0).sum().item()))
            else:
                outputs = torch.nn.functional.softmax(outputs, dim=1)
                _, predicted = outputs.max(1)

                if N_classes == 2:
                    scores_test = np.concatenate((scores_test, outputs[:, 1].cpu().detach().numpy()))
                else:
                    scores_test = np.hstack((scores_test, outputs.cpu().detach().numpy().transpose()))

                true_labels_test = np.concatenate((true_labels_test, targets.cpu().detach().numpy()))
                correct_labeling_test += predicted.eq(targets).sum().item()
                total_pos_test += targets.eq(1).sum().item()
                total_neg_test += targets.eq(0).sum().item()
                true_pos_test += predicted[targets.eq(1)].eq(1).sum().item()
                true_neg_test += predicted[targets.eq(0)].eq(0).sum().item()
            total_test += targets.size(0)

        #perform slide inference
        if (not multi_target) and (N_classes == 2):
            patch_df = pd.DataFrame({'slide': slide_names, 'scores': scores_test, 'labels': true_labels_test})
            slide_mean_score_df = patch_df.groupby('slide').mean()
            roc_auc_slide = np.nan
            if not all(slide_mean_score_df['labels'] == slide_mean_score_df['labels'][0]): #more than one label
                roc_auc_slide = roc_auc_score(slide_mean_score_df['labels'], slide_mean_score_df['scores'])

        if args.bootstrap and (not multi_target) and (N_classes == 2): #does not support multitarget, multiclass!
            # load dataset
            # configure bootstrap
            n_iterations = 100

            # run bootstrap
            roc_auc_array = np.empty(n_iterations)
            slide_roc_auc_array = np.empty(n_iterations)
            roc_auc_array[:], slide_roc_auc_array[:] = np.nan, np.nan
            acc_array, bacc_array = np.empty(n_iterations), np.empty(n_iterations)
            acc_array[:], bacc_array[:] = np.nan, np.nan

            all_preds = np.array([int(score > 0.5) for score in scores_test])

            for ii in range(n_iterations):
                slide_names = np.array(slide_names)
                slide_choice = resample(np.unique(np.array(slide_names)))
                slide_resampled = np.concatenate([slide_names[slide_names == slide] for slide in slide_choice])
                scores_resampled = np.concatenate([scores_test[slide_names == slide] for slide in slide_choice])
                labels_resampled = np.concatenate([true_labels_test[slide_names == slide] for slide in slide_choice])
                preds_resampled = np.concatenate([all_preds[slide_names == slide] for slide in slide_choice])
                patch_df = pd.DataFrame({'slide': slide_resampled, 'scores': scores_resampled, 'labels': labels_resampled})

                num_correct_i = np.sum(preds_resampled == labels_resampled)
                true_pos_i = np.sum(labels_resampled + preds_resampled == 2)
                total_pos_i = np.sum(labels_resampled == 1)
                true_neg_i = np.sum(labels_resampled + preds_resampled == 0)
                total_neg_i = np.sum(labels_resampled == 0)
                tot = total_pos_i + total_neg_i
                acc_array[ii] = 100 * float(num_correct_i) / tot
                bacc_array[ii] = 100. * ((true_pos_i + EPS) / (total_pos_i + EPS) + (true_neg_i + EPS) / (total_neg_i + EPS)) / 2
                fpr, tpr, _ = roc_curve(labels_resampled, scores_resampled)
                if not all(labels_resampled == labels_resampled[0]): #more than one label
                    roc_auc_array[ii] = roc_auc_score(labels_resampled, scores_resampled)

                slide_mean_score_df = patch_df.groupby('slide').mean()
                if not all(slide_mean_score_df['labels'] == slide_mean_score_df['labels'][0]):  # more than one label
                    slide_roc_auc_array[ii] = roc_auc_score(slide_mean_score_df['labels'], slide_mean_score_df['scores'])

            roc_auc_std = np.nanstd(roc_auc_array)
            roc_auc_slide_std = np.nanstd(slide_roc_auc_array)
            acc_err = np.nanstd(acc_array)
            bacc_err = np.nanstd(bacc_array)

            all_writer.add_scalar('Test_errors/Accuracy error', acc_err, epoch)
            all_writer.add_scalar('Test_errors/Balanced Accuracy error', bacc_err, epoch)
            all_writer.add_scalar('Test_errors/Roc-Auc error', roc_auc_std, epoch)
            if args.n_patches_test > 1:
                all_writer.add_scalar('Test_errors/slide AUC error', roc_auc_slide_std, epoch)

        #acc = 100 * float(correct_labeling_test) / total_test
        acc = 100 * correct_labeling_test / total_test
        bacc = 100. * ((true_pos_test + EPS) / (total_pos_test + EPS) + (true_neg_test + EPS) / (total_neg_test + EPS)) / 2
        if multi_target:
            roc_auc = np.empty(N_targets)
            roc_auc[:] = np.nan
            for i_target in range(N_targets):
                if len(np.unique(true_labels_test[i_target, true_labels_test[i_target, :] >= 0])) > 1:  # more than one label
                    fpr_train, tpr_train, _ = roc_curve(true_labels_test[i_target, true_labels_test[i_target, :] >= 0],
                                                        scores_test[i_target, true_labels_test[i_target, :] >= 0])
                    roc_auc[i_target] = auc(fpr_train, tpr_train)
                    all_writer.add_scalars('Test/Balanced Accuracy', {target_list[i_target]: bacc[i_target]}, epoch)
                    all_writer.add_scalars('Test/Roc-Auc', {target_list[i_target]: roc_auc[i_target]}, epoch)
                    all_writer.add_scalars('Test/Accuracy', {target_list[i_target]: acc[i_target]}, epoch)

            '''if len(np.unique(true_labels_test[0, true_labels_test[0, :] >= 0])) > 1:  # more than one label
                fpr_train, tpr_train, _ = roc_curve(true_labels_test[0, true_labels_test[0, :] >= 0],
                                                    scores_test[0, true_labels_test[0, :] >= 0])
                roc_auc[0] = auc(fpr_train, tpr_train)
            if len(np.unique(true_labels_test[1, true_labels_test[1, :] >= 0])) > 1:  # more than one label
                fpr_train, tpr_train, _ = roc_curve(true_labels_test[1, true_labels_test[1, :] >= 0],
                                                    scores_test[1, true_labels_test[1, :] >= 0])
                roc_auc[1] = auc(fpr_train, tpr_train)
            all_writer.add_scalars('Test/Balanced Accuracy',
                                   {target0: bacc[0], target1: bacc[1]}, epoch)
            all_writer.add_scalars('Test/Roc-Auc', {target0: roc_auc[0], target1: roc_auc[1]}, epoch)
            all_writer.add_scalars('Test/Accuracy', {target0: acc[0], target1: acc[1]}, epoch)'''
        elif N_classes > 2:  # multiclass
            # calculate binary AUC scores for each threshold
            for i_thresh in range(N_classes - 1):
                roc_auc = np.float64(np.nan)
                N_pos = np.sum(true_labels_test >= (i_thresh + 1))  # num of positive targets
                N_neg = np.sum(true_labels_test < (i_thresh + 1))  # num of negative targets
                if (N_pos > 0) and (N_neg > 0):  # more than one label
                    scores_test_thresh = np.sum(scores_test[i_thresh + 1:, :], axis=0)
                    true_targets_test_thresh = true_labels_test >= (i_thresh + 1)
                    fpr, tpr, _ = roc_curve(true_targets_test_thresh, scores_test_thresh)
                    roc_auc = auc(fpr, tpr)
                all_writer.add_scalar('Test/Roc-Auc_thresh_' + str(i_thresh), roc_auc, epoch)
            all_writer.add_scalar('Test/Accuracy', acc, epoch)
            all_writer.add_scalar('Test/Balanced Accuracy', bacc, epoch)
        else:
            roc_auc = np.float64(np.nan)
            if not all(true_labels_test == true_labels_test[0]):  # more than one label
                fpr, tpr, _ = roc_curve(true_labels_test, scores_test)
                roc_auc = auc(fpr, tpr)

            all_writer.add_scalar('Test/Accuracy', acc, epoch)
            all_writer.add_scalar('Test/Balanced Accuracy', bacc, epoch)
            all_writer.add_scalar('Test/Roc-Auc', roc_auc, epoch)
            if args.n_patches_test > 1:
                all_writer.add_scalar('Test/slide AUC', roc_auc_slide, epoch)

            if args.n_patches_test > 1:
                #print('Slide AUC of {:.2f} over Test set'.format(roc_auc_slide))
                logging.info('Slide AUC of {:.2f} over Test set'.format(roc_auc_slide))
            else:
                #print('Tile AUC of {:.2f} over Test set'.format(roc_auc))
                logging.info('Tile AUC of {:.2f} over Test set'.format(roc_auc))
    model.train()
    return acc, bacc, roc_auc

########################################################################################################
########################################################################################################

if __name__ == '__main__':
    # Device definition:

    DEVICE = utils.device_gpu_cpu()

    # Tile size definition:
    TILE_SIZE = 128

    if sys.platform == 'linux' or sys.platform == 'win32':
        TILE_SIZE = 256

    if (args.dataset == 'TMA') and (args.mag == 7):
        TILE_SIZE = 512

    # Saving/Loading run meta data to/from file:
    if args.experiment == 0:
        run_data_results = utils.run_data(test_fold=args.test_fold,
                                                     #transformations=args.transformation,
                                                     transform_type=args.transform_type,
                                                     tile_size=TILE_SIZE,
                                                     tiles_per_bag=1,
                                                     DX=args.dx,
                                                     DataSet_name=args.dataset,
                                                     Receptor=args.target,
                                                     num_bags=args.batch_size)

        args.output_dir, experiment = run_data_results['Location'], run_data_results['Experiment']
    else:
        run_data_output = utils.run_data(experiment=args.experiment)
        args.output_dir, args.test_fold, args.transform_type, TILE_SIZE, tiles_per_bag, \
        batch_size_saved, args.dx, args.dataset, args.target, prev_model_name, args.mag =\
            run_data_output['Location'], run_data_output['Test Fold'], run_data_output['Transformations'], run_data_output['Tile Size'],\
            run_data_output['Tiles Per Bag'], run_data_output['Num Bags'], run_data_output['DX'], run_data_output['Dataset Name'],\
            run_data_output['Receptor'], run_data_output['Model Name'], run_data_output['Desired Slide Magnification']

        # if batch size is not default, override the saved value
        # this is necessary when switching nodes
        if args.batch_size == DEFAULT_BATCH_SIZE:
            args.batch_size = batch_size_saved

        #args.model = prev_model_name #temp cancelled RanS 3.1.21
        print('args.dataset:', args.dataset)
        print('args.target:', args.target)
        print('args.batch_size:', args.batch_size)
        print('args.output_dir:', args.output_dir)
        print('args.test_fold:', args.test_fold)
        print('args.transform_type:', args.transform_type)
        print('args.dx:', args.dx)

        experiment = args.experiment

    start_log(args)

    # Get number of available CPUs and compute number of workers:
    cpu_available = utils.get_cpu()
    num_workers = cpu_available
    #num_workers = cpu_available * 2 #temp RanS 9.8.21
    #num_workers = cpu_available//2  # temp RanS 9.8.21
    #num_workers = 4 #temp RanS 24.3.21

    if sys.platform == 'win32':
        num_workers = 0 #temp RanS 3.5.21

    #print('num workers = ', num_workers)
    logging.info('num CPUs = {}'.format(cpu_available))
    logging.info('num workers = {}'.format(num_workers))

    # Get data:
    train_dset = datasets.WSI_REGdataset(DataSet=args.dataset,
                                         tile_size=TILE_SIZE,
                                         target_kind=args.target,
                                         test_fold=args.test_fold,
                                         train=True,
                                         print_timing=args.time,
                                         transform_type=args.transform_type,
                                         n_tiles=args.n_patches_train,
                                         color_param=args.c_param,
                                         get_images=args.images,
                                         desired_slide_magnification=args.mag,
                                         DX=args.dx,
                                         loan=args.loan,
                                         er_eq_pr=args.er_eq_pr,
                                         slide_per_block=args.slide_per_block,
                                         balanced_dataset=args.balanced_dataset,
                                         RAM_saver=args.RAM_saver
                                         )
    test_dset = datasets.WSI_REGdataset(DataSet=args.dataset,
                                        tile_size=TILE_SIZE,
                                        target_kind=args.target,
                                        test_fold=args.test_fold,
                                        train=False,
                                        print_timing=False,
                                        transform_type='none',
                                        n_tiles=args.n_patches_test,
                                        get_images=args.images,
                                        desired_slide_magnification=args.mag,
                                        DX=args.dx,
                                        loan=args.loan,
                                        er_eq_pr=args.er_eq_pr,
                                        RAM_saver=args.RAM_saver
                                        )
    sampler = None
    do_shuffle = True
    if args.balanced_sampling:
        labels = pd.DataFrame(train_dset.target * train_dset.factor)
        n_pos = np.sum(labels == 'Positive').item()
        n_neg = np.sum(labels == 'Negative').item()
        weights = pd.DataFrame(np.zeros(len(train_dset)))
        weights[np.array(labels == 'Positive')] = 1 / n_pos
        weights[np.array(labels == 'Negative')] = 1 / n_neg
        do_shuffle = False  # the sampler shuffles
        sampler = torch.utils.data.sampler.WeightedRandomSampler(weights=weights.squeeze(), num_samples=len(train_dset))

    train_loader = DataLoader(train_dset, batch_size=args.batch_size, shuffle=do_shuffle,
                              num_workers=num_workers, pin_memory=True, sampler=sampler)
    test_loader  = DataLoader(test_dset, batch_size=args.batch_size*2, shuffle=False,
                              num_workers=num_workers, pin_memory=True)

    # Save transformation data to 'run_data.xlsx'
    transformation_string = ', '.join([str(train_dset.transform.transforms[i]) for i in range(len(train_dset.transform.transforms))])
    utils.run_data(experiment=experiment, transformation_string=transformation_string)

    # Load model
    model = eval(args.model)
    if args.target == 'Survival_Time':
        model.change_num_classes(num_classes=1)  # This will convert the liner (classifier) layer into the beta layer
        model.model_name += '_Continous_Time'

    try:
        N_classes = model.linear.out_features #for resnets and such
    except:
        N_classes = model._final_1x1_conv.out_channels #for StereoSphereRes

    # Save model data and data-set size to run_data.xlsx file (Only if this is a new run).
    if args.experiment == 0:
        utils.run_data(experiment=experiment, model=model.model_name)
        utils.run_data(experiment=experiment, DataSet_size=(train_dset.real_length, test_dset.real_length))
        utils.run_data(experiment=experiment, DataSet_Slide_magnification=train_dset.desired_magnification)

        # Saving code files, args and main file name (this file) to Code directory within the run files.
        utils.save_code_files(args, train_dset)

    epoch = args.epochs
    from_epoch = args.from_epoch

    # In case we continue from an already trained model, than load the previous model and optimizer data:
    if args.experiment != 0:
        print('Loading pre-saved model...')
        if from_epoch == 0: #RanS 25.7.21, load last epoch
            model_data_loaded = torch.load(os.path.join(args.output_dir,
                                                        'Model_CheckPoints',
                                                        'model_data_Last_Epoch.pt'),
                                           map_location='cpu')
            from_epoch = model_data_loaded['epoch'] + 1
        else:
            model_data_loaded = torch.load(os.path.join(args.output_dir,
                                                        'Model_CheckPoints',
                                                        'model_data_Epoch_' + str(args.from_epoch) + '.pt'), map_location='cpu')
            from_epoch = args.from_epoch + 1
        model.load_state_dict(model_data_loaded['model_state_dict'])

        print()
        print('Resuming training of Experiment {} from Epoch {}'.format(args.experiment, from_epoch))

    elif args.transfer_learning != '':
        #use model trained on another experiment
        #transfer_learning should be of the form 'ex=390,epoch=1000'
        ex_str, epoch_str = args.transfer_learning.split(',')
        ex_model = int(re.sub("[^0-9]", "", ex_str))
        epoch_model = int(re.sub("[^0-9]", "", epoch_str))

        run_data_model = utils.run_data(experiment=ex_model)
        model_dir = run_data_model['Location']
        model_data_loaded = torch.load(os.path.join(model_dir,
                                                    'Model_CheckPoints',
                                                    'model_data_Epoch_' + str(epoch_model) + '.pt'),
                                       map_location='cpu')
        try:
            model.load_state_dict(model_data_loaded['model_state_dict'])
        except:
            raise IOError('Cannot load the saved transfer_learning model, check if it fits the current model')


    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    '''if (args.dataset == 'TMA') and (args.mag == 7): #temp RanS 13.1.22
        modules = [{'params': model.parameters(), 'weight_decay': args.weight_decay}]
        #optimizer = optim.SGD(modules, lr=args.lr, momentum=0.9, weight_decay=args.weight_decay, nesterov=True)
        optimizer = optim.RAdam(modules, lr=args.lr, weight_decay=args.weight_decay) #RanS 7.3.22
        scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=60, gamma=0.1)'''

    if len(train_dset.target_kind) > 1:
        multi_target = True
        target_list = train_dset.target_kind
        N_targets = len(target_list)
        #target0, target1 = args.target.split('+')
        if model.linear.out_features != 2*len(target_list):
            raise IOError('Model defined does not match number of targets, select model with ' + str(2*len(target_list)) + ' output channels')
        #if model.linear.out_features != 4:
            #raise IOError('Model defined does not support multitarget training, select model with 4 output channels')
    else:
        multi_target = False

    if DEVICE.type == 'cuda':
        model = torch.nn.DataParallel(model) #DataParallel, RanS 1.8.21.
        cudnn.benchmark = True

        # RanS 28.1.21
        # https://forums.fast.ai/t/show-gpu-utilization-metrics-inside-training-loop-without-subprocess-call/26594
        if args.time:
            import nvidia_smi
            nvidia_smi.nvmlInit()
            handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
            # card id 0 hardcoded here, there is also a call to get all available card ids, so we could iterate

    if args.experiment != 0:
        optimizer.load_state_dict(model_data_loaded['optimizer_state_dict'])
        for state in optimizer.state.values():
            for k, v in state.items():
                if torch.is_tensor(v):
                    state[k] = v.to(DEVICE)

    if args.focal:
        criterion = utils.FocalLoss(gamma=2)
        criterion.to(DEVICE)
    elif args.target == 'Survival_Time':
        criterion = Cox_loss
    else:
        if multi_target:
            criterion = nn.CrossEntropyLoss(ignore_index=-1, reduction='none')
        else:
            criterion = nn.CrossEntropyLoss()

    if args.RAM_saver:
        shuffle_freq = 100 #reshuffle dataset every 200 epochs
        shuffle_epoch_list = np.arange(np.ceil((from_epoch+EPS) / shuffle_freq) * shuffle_freq, epoch, shuffle_freq).astype(int)
        shuffle_epoch_list = np.append(shuffle_epoch_list, epoch)

        epoch = shuffle_epoch_list[0]
        train(model, train_loader, test_loader, DEVICE=DEVICE, optimizer=optimizer, print_timing=args.time)

        for from_epoch, epoch in zip(shuffle_epoch_list[:-1], shuffle_epoch_list[1:]):
            print('Reshuffling dataset:')
            #shuffle train and test set to get new slides
            # Get data:
            train_dset = datasets.WSI_REGdataset(DataSet=args.dataset,
                                                 tile_size=TILE_SIZE,
                                                 target_kind=args.target,
                                                 test_fold=args.test_fold,
                                                 train=True,
                                                 print_timing=args.time,
                                                 transform_type=args.transform_type,
                                                 n_tiles=args.n_patches_train,
                                                 color_param=args.c_param,
                                                 get_images=args.images,
                                                 desired_slide_magnification=args.mag,
                                                 DX=args.dx,
                                                 loan=args.loan,
                                                 er_eq_pr=args.er_eq_pr,
                                                 slide_per_block=args.slide_per_block,
                                                 balanced_dataset=args.balanced_dataset,
                                                 RAM_saver=args.RAM_saver
                                                 )
            test_dset = datasets.WSI_REGdataset(DataSet=args.dataset,
                                                tile_size=TILE_SIZE,
                                                target_kind=args.target,
                                                test_fold=args.test_fold,
                                                train=False,
                                                print_timing=False,
                                                transform_type='none',
                                                n_tiles=args.n_patches_test,
                                                get_images=args.images,
                                                desired_slide_magnification=args.mag,
                                                DX=args.dx,
                                                loan=args.loan,
                                                er_eq_pr=args.er_eq_pr,
                                                RAM_saver=args.RAM_saver
                                                )
            sampler = None
            do_shuffle = True
            if args.balanced_sampling:
                labels = pd.DataFrame(train_dset.target * train_dset.factor)
                n_pos = np.sum(labels == 'Positive').item()
                n_neg = np.sum(labels == 'Negative').item()
                weights = pd.DataFrame(np.zeros(len(train_dset)))
                weights[np.array(labels == 'Positive')] = 1 / n_pos
                weights[np.array(labels == 'Negative')] = 1 / n_neg
                do_shuffle = False  # the sampler shuffles
                sampler = torch.utils.data.sampler.WeightedRandomSampler(weights=weights.squeeze(),
                                                                         num_samples=len(train_dset))

            train_loader = DataLoader(train_dset, batch_size=args.batch_size, shuffle=do_shuffle,
                                      num_workers=num_workers, pin_memory=True, sampler=sampler)
            test_loader = DataLoader(test_dset, batch_size=args.batch_size * 2, shuffle=False,
                                     num_workers=num_workers, pin_memory=True)

            print('resuming training with new dataset')
            train(model, train_loader, test_loader, DEVICE=DEVICE, optimizer=optimizer, print_timing=args.time)
    else:
        train(model, train_loader, test_loader, DEVICE=DEVICE, optimizer=optimizer, print_timing=args.time)

    #finished training, send email if possible
    if os.path.isfile('mail_cfg.txt'):
        with open("mail_cfg.txt", "r") as f:
            text = f.readlines()
            receiver_email = text[0][:-1]
            password = text[1]

        port = 465  # For SSL
        sender_email = "gipmed.python@gmail.com"

        message = 'Subject: finished running experiment ' + str(experiment)

        # Create a secure SSL context
        context = ssl.create_default_context()

        with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context) as server:
            server.login(sender_email, password)
            server.sendmail(sender_email, receiver_email, message)
            print('email sent to ' + receiver_email)
