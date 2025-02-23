import Omer_files_suspected_as_unnecessary.omer_datasets
import utils
import datasets
from torch.utils.data import DataLoader
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch
import torch.optim as optim
from Nets.nets_mil import ResNet50_GN_GatedAttention
from tqdm import tqdm
import time
from torch.utils.tensorboard import SummaryWriter
import argparse
import os
from sklearn.metrics import roc_curve, auc
import numpy as np
import sys
import pandas as pd

parser = argparse.ArgumentParser(description='WSI_MIL Training of PathNet Project')
parser.add_argument('-tf', '--test_fold', default=2, type=int, help='fold to be as TEST FOLD')
parser.add_argument('-e', '--epochs', default=3, type=int, help='Epochs to run')
#parser.add_argument('-t', dest='transformation', action='store_true', help='Include transformations ?')
parser.add_argument('-tt', '--transform_type', type=str, default='flip', help='keyword for transform type')
parser.add_argument('-ex', '--experiment', type=int, default=0, help='Continue train of this experiment')
parser.add_argument('-fe', '--from_epoch', type=int, default=0, help='Continue train from epoch')
parser.add_argument('-d', dest='dx', action='store_true', help='Use ONLY DX cut slides')
parser.add_argument('-ms', dest='multi_slides', action='store_true', help='Use more than one slide in each bag')
parser.add_argument('-ds', '--dataset', type=str, default='RedSquares', help='DataSet to use')
parser.add_argument('-lf', '--look_for', type=str, default='RedSquares', help='DataSet to use')
parser.add_argument('-im', dest='images', action='store_true', help='save data images?')
parser.add_argument('-time', dest='time', action='store_true', help='save train timing data ?')



args = parser.parse_args()

"""
def simple_train(model: nn.Module, dloader_train: DataLoader, dloadet_test: DataLoader):

    print('Start Training...')
    for e in range(epoch):
        print('Epoch {}:'.format(e))
        correct_train, num_samples, train_loss = 0, 0, 0

        model.train()
        for idx, (data, target) in enumerate(tqdm(dloader_train)):
            data, target = data.to(DEVICE), target.to(DEVICE)
            model.to(DEVICE)
            prob, label, weights = model(data)
            correct_train += (label == target).data.cpu().int().item()
            num_samples += 1

            prob = torch.clamp(prob, min=1e-5, max=1. - 1e-5)
            loss = -1. * (target * torch.log(prob) + (1. - target) * torch.log(1. - prob))  # negative log bernoulli
            train_loss += loss.data.cpu().item()
            # criterion = nn.CrossEntropyLoss()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        train_accuracy = 100 * float(correct_train) / num_samples
        print('Finished Epoch: {}, Train Accuracy: {:.2f}% ({}/{}), Loss: {:.2f}'.format(e,
                                                                                         train_accuracy,
                                                                                         correct_train,
                                                                                         num_samples,
                                                                                         train_loss))
        print('Checking post-epoch accuracy over training set...')
        correct_post = 0
        num_sample_post = 0
        with torch.no_grad():
            #model.eval()
            for idx, (data_post, target_post) in enumerate(train_loader):
                data_post, target_post = data_post.to(DEVICE), target_post.to(DEVICE)

                prob_post, label_post, weights_post = model(data_post)
                correct_post += (label_post == target_post).data.cpu().int().item()
                num_sample_post += 1

            accuracy_post_train = 100 * float(correct_post) / num_sample_post
            print('Post train accuracy: {:.2f}% ({} / {})'.format(accuracy_post_train, correct_post, num_sample_post))
"""

def norm_img(img):
    img -= img.min()
    img /= img.max()
    return img


def train(model: nn.Module, dloader_train: DataLoader, dloader_test: DataLoader, DEVICE, optimizer, print_timing: bool=False):
    """
    This function trains the model
    :return:
    """
    writer_folder = os.path.join(args.output_dir, 'writer')
    all_writer = SummaryWriter(os.path.join(writer_folder, 'all'))
    image_writer = SummaryWriter(os.path.join(writer_folder, 'image'))

    if from_epoch == 0:
        all_writer.add_text('Experiment No.', str(experiment))
        all_writer.add_text('Train type', 'MIL')
        all_writer.add_text('Model type', str(type(model)))
        all_writer.add_text('Data type', dloader_train.dataset.DataSet)
        all_writer.add_text('Train Folds', str(dloader_train.dataset.folds).strip('[]'))
        all_writer.add_text('Test Folds', str(dloader_test.dataset.folds).strip('[]'))
        all_writer.add_text('Transformations', str(dloader_train.dataset.transform))
        all_writer.add_text('Receptor Type', str(dloader_train.dataset.target_kind))


    if print_timing:
        time_writer = SummaryWriter(os.path.join(writer_folder, 'time'))

    print('Start Training...')
    best_train_loss = 1e5
    previous_epoch_loss = 1e5
    best_model = None

    '''    
    # The following part saves the random slides on file for further debugging
    if os.path.isfile('random_slides.xlsx'):
        random_slides = pd.read_excel('random_slides.xlsx')
    else:
        random_slides = pd.DataFrame()
    ###############################################
    '''

    for e in range(from_epoch, epoch + from_epoch):
        time_epoch_start = time.time()
        if e == 0:
            data_list = []
            # slide_random_list = []

        # The following 3 lines initialize variables to compute AUC for train dataset.
        total_pos_train, total_neg_train = 0, 0
        true_pos_train, true_neg_train = 0, 0
        targets_train, scores_train = np.zeros(len(dloader_train), dtype=np.int8), np.zeros(len(dloader_train))
        correct_labeling, train_loss = 0, 0

        print('Epoch {}:'.format(e))
        model.train()
        model.infer = False
        for batch_idx, (data, target, time_list, image_file, basic_tiles) in enumerate(tqdm(dloader_train)):
            train_start = time.time()
            '''
            if args.images:
                step = batch_idx + e * 1000
                image_writer.add_images('Train Images/Before Transforms', basic_tiles.squeeze().detach().cpu().numpy(),
                                    global_step=step, dataformats='NCHW')
            '''

            '''
            # The following section is responsible for saving the random slides for it's iteration - For debbugging purposes 
            slide_dict = {'Epoch': e,
                          'Main Slide index': idxx.cpu().detach().numpy()[0],
                          'random Slides index': slides_idx_other}
            slide_random_list.append(slide_dict)
            '''

            if e == 0:
                data_dict = { 'File Name':  image_file,
                              'Target': target.cpu().detach().item()
                              }
                data_list.append(data_dict)

            data, target = data.to(DEVICE), target.to(DEVICE)
            model.to(DEVICE)

            prob, label, _ = model(data)

            targets_train[batch_idx] = target.cpu().detach().item()
            total_pos_train += target.eq(1).item()
            total_neg_train += target.eq(0).item()

            if target == 1 and label == 1:
                true_pos_train += 1
            elif target == 0 and label == 0:
                true_neg_train += 1

            scores_train[batch_idx] = prob.cpu().detach().numpy()[0][0]

            prob = torch.clamp(prob, min=1e-5, max=1. - 1e-5)
            neg_log_likelihood = -1. * (target * torch.log(prob) + (1. - target) * torch.log(1. - prob))  # negative log bernoulli
            loss = neg_log_likelihood
            train_loss += loss.item()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            all_writer.add_scalar('Loss', loss.data[0], batch_idx + e * len(dloader_train))

            # Calculate training accuracy
            correct_labeling += label.eq(target).cpu().detach().int().item()

            train_time = time.time() - train_start
            if print_timing:
                time_stamp = batch_idx + e * len(dloader_train)
                time_writer.add_scalar('Time/Train (iter) [Sec]', train_time, time_stamp)
                # print('Elapsed time of one train iteration is {:.2f} s'.format(train_time))
                if len(time_list) == 4:
                    time_writer.add_scalar('Time/Open WSI [Sec]'     , time_list[0], time_stamp)
                    time_writer.add_scalar('Time/Avg to Extract Tile [Sec]', time_list[1], time_stamp)
                    time_writer.add_scalar('Time/Augmentation [Sec]' , time_list[2], time_stamp)
                    time_writer.add_scalar('Time/Total To Collect Data [Sec]', time_list[3], time_stamp)
                else:
                    time_writer.add_scalar('Time/Avg to Extract Tile [Sec]', time_list[0], time_stamp)
                    time_writer.add_scalar('Time/Augmentation [Sec]', time_list[1], time_stamp)
                    time_writer.add_scalar('Time/Total To Collect Data [Sec]', time_list[2], time_stamp)

        '''
        random_slides = random_slides.append(pd.DataFrame(slide_random_list))
        random_slides.to_excel('random_slides.xlsx')
        '''
        time_epoch = (time.time() - time_epoch_start) / 60
        if print_timing:
            time_writer.add_scalar('Time/Full Epoch [min]', time_epoch, e)

        train_acc = 100 * correct_labeling / len(dloader_train)

        balanced_acc_train = 100 * (true_pos_train / total_pos_train + true_neg_train / total_neg_train) / 2

        fpr_train, tpr_train, _ = roc_curve(targets_train, scores_train)
        roc_auc_train = auc(fpr_train, tpr_train)

        all_writer.add_scalar('Train/Balanced Accuracy', balanced_acc_train, e)
        all_writer.add_scalar('Train/Roc-Auc', roc_auc_train, e)
        all_writer.add_scalar('Train/Loss Per Epoch', train_loss, e)
        all_writer.add_scalar('Train/Accuracy', train_acc, e)

        print('Finished Epoch: {}, Loss: {:.2f}, Loss Delta: {:.3f}, Train Accuracy: {:.2f}% ({} / {}), Time: {:.0f} m'
              .format(e,
                      train_loss,
                      previous_epoch_loss - train_loss,
                      train_acc,
                      int(correct_labeling),
                      len(train_loader),
                      time_epoch))

        previous_epoch_loss = train_loss

        if train_loss < best_train_loss:
            best_train_loss = train_loss
            best_train_acc = train_acc
            best_epoch = e
            best_model = model

        if e % 5 == 0:
            acc_test, bacc_test = check_accuracy(model, dloader_test, all_writer, image_writer, DEVICE, e,
                                                 eval_mode=True)
            ### acc_test, bacc_test = check_accuracy(model, dloader_test, all_writer, image_writer, DEVICE, e, eval_mode=False)
            # Update 'Last Epoch' at run_data.xlsx file:
            utils.run_data(experiment=experiment, epoch=e)
            if e % 20 == 0 and args.images:
                image_writer.add_images('Train Images/Before Transforms', basic_tiles.squeeze().detach().cpu().numpy(),
                                        global_step=e, dataformats='NCHW')
                image_writer.add_images('Train Images/After Transforms', data.squeeze().detach().cpu().numpy(),
                                        global_step=e, dataformats='NCHW')
                image_writer.add_images('Train Images/After Transforms (De-Normalized)',
                                        norm_img(data.squeeze().detach().cpu().numpy()), global_step=e,
                                        dataformats='NCHW')
        else:
            acc_test, bacc_test = None, None



        # Save model to file:
        if not os.path.isdir(os.path.join(args.output_dir, 'Model_CheckPoints')):
            os.mkdir(os.path.join(args.output_dir, 'Model_CheckPoints'))

        try:
            model_state_dict = model.module.state_dict()
        except AttributeError:
            model_state_dict = model.state_dict()

        torch.save({'epoch': e,
                    'model_state_dict': model_state_dict,
                    'optimizer_state_dict': optimizer.state_dict(),
                    'loss': loss.data[0],
                    'acc_test': acc_test,
                    'bacc_test': bacc_test,
                    'tile_size': TILE_SIZE,
                    'tiles_per_bag': TILES_PER_BAG},
                   os.path.join(args.output_dir, 'Model_CheckPoints', 'model_data_Epoch_' + str(e) + '.pt'))

        if e == 0:
            pd.DataFrame(data_list).to_excel('validate_data.xlsx')
            print('Saved validation data')

    all_writer.close()
    if print_timing:
        time_writer.close()
    '''
    # If epochs ended - Save best model:
    if not os.path.isdir(os.path.join(args.output_dir, 'Model_CheckPoints')):
        os.mkdir(os.path.join(args.output_dir, 'Model_CheckPoints'))

    try:
        best_model_state_dict = best_model.module.state_dict()
    except AttributeError:
        best_model_state_dict = best_model.state_dict()

    torch.save({'epoch': best_epoch,
                'model_state_dict': best_model_state_dict,
                'best_train_loss': best_train_loss,
                'best_train_acc': best_train_acc,
                'tile_size': TILE_SIZE,
                'tiles_per_bag': TILES_PER_BAG},
               os.path.join(args.output_dir, 'Model_CheckPoints', 'best_model_Ep_' + str(best_epoch) + '.pt'))

    '''



def check_accuracy(model: nn.Module, data_loader: DataLoader, writer_all, image_writer, DEVICE, epoch: int, eval_mode: bool = False):
    num_correct = 0
    total_pos, total_neg = 0, 0
    true_pos, true_neg = 0, 0
    targets_test, scores = np.zeros(len(data_loader)), np.zeros(len(data_loader))

    if eval_mode:
        model.eval()
        '''
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.track_running_stats = False
        '''
    else:
        model.train()
        '''
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.track_running_stats = False
        '''
    with torch.no_grad():
        for idx, (data, target, time_list, _, basic_tiles) in enumerate(data_loader):
            if epoch % 20 == 0 and args.images:
                image_writer.add_images('Test Images/Before Transforms', basic_tiles.squeeze().detach().cpu().numpy(),
                                     global_step=epoch, dataformats='NCHW')
                image_writer.add_images('Test Images/After Transforms', data.squeeze().detach().cpu().numpy(),
                                     global_step=epoch, dataformats='NCHW')
                image_writer.add_images('Test Images/After Transforms (De-Normalized)',
                                     norm_img(data.squeeze().detach().cpu().numpy()), global_step=epoch, dataformats='NCHW')

            data, target = data.to(device=DEVICE), target.to(device=DEVICE)
            model.to(DEVICE)

            prob, label, _ = model(data)

            targets_test[idx] = target.cpu().detach().item()
            total_pos += target.eq(1).item()
            total_neg += target.eq(0).item()

            if target == 1 and label == 1:
                true_pos += 1
            elif target == 0 and label == 0:
                true_neg += 1

            num_correct += label.eq(target).cpu().detach().int().item()
            scores[idx] = prob.cpu().detach().item()

        acc = 100 * float(num_correct) / len(data_loader)
        balanced_acc = 100 * (true_pos / (total_pos + 1e-7) + true_neg / (total_neg + 1e-7)) / 2

        fpr, tpr, _ = roc_curve(targets_test, scores)
        roc_auc = auc(fpr, tpr)

        if data_loader.dataset.train:
            writer_string = 'Train_2'
        else:
            if eval_mode:
                writer_string = 'Test (eval mode)'
            else:
                writer_string = 'Test (train mode)'

        writer_all.add_scalar(writer_string + '/Accuracy', acc, epoch)
        writer_all.add_scalar(writer_string + '/Balanced Accuracy', balanced_acc, epoch)
        writer_all.add_scalar(writer_string + '/Roc-Auc', roc_auc, epoch)

        print('{}: Accuracy of {:.2f}% ({} / {}) over Test set'.format('EVAL mode' if eval_mode else 'TRAIN mode', acc, num_correct, len(data_loader)))

    model.train()
    '''
    for module in model.modules():
        if isinstance(module, nn.BatchNorm2d):
            module.track_running_stats = False
    '''
    return acc, balanced_acc



##################################################################################################


if __name__ == '__main__':
    # Device definition:
    DEVICE = utils.device_gpu_cpu()
    # Data type definition:
    DATA_TYPE = 'WSI'

    # Tile size definition:
    TILE_SIZE =128
    TILES_PER_BAG = 10
    # data_path = '/Users/wasserman/Developer/All data - outer scope'

    if sys.platform == 'linux':
        TILE_SIZE = 256
        TILES_PER_BAG = 50
        # data_path = '/home/womer/project/All Data'

    # Saving/Loading run meta data to/from file:
    if args.experiment == 0:
        args.output_dir, experiment = utils.run_data(test_fold=args.test_fold,
                                                     transform_type=args.transform_type,
                                                     tile_size=TILE_SIZE,
                                                     tiles_per_bag=TILES_PER_BAG,
                                                     DX=args.dx,
                                                     DataSet_name=args.dataset,
                                                     Receptor=args.look_for,
                                                     MultiSlide=args.multi_slides)
    else:
        args.output_dir, args.test_fold, args.transformation, TILE_SIZE, TILES_PER_BAG, _, args.dx,\
            args.dataset, args.look_for, args.multi_slides = utils.run_data(experiment=args.experiment)
        experiment = args.experiment

    # Get number of available CPUs:
    cpu_available = utils.get_cpu()

    # Get data:
    if not args.multi_slides:
        train_dset = Omer_files_suspected_as_unnecessary.omer_datasets.WSI_MILdataset(DataSet=args.dataset,
                                                                                      tile_size=TILE_SIZE,
                                                                                      bag_size=TILES_PER_BAG,
                                                                                      target_kind=args.look_for,
                                                                                      test_fold=args.test_fold,
                                                                                      train=True,
                                                                                      print_timing=args.time,
                                                                                      transform_type=args.transform_type,
                                                                                      DX=args.dx,
                                                                                      get_images=args.images)

        test_dset = Omer_files_suspected_as_unnecessary.omer_datasets.WSI_MILdataset(DataSet=args.dataset,
                                                                                     tile_size=TILE_SIZE,
                                                                                     bag_size=TILES_PER_BAG,
                                                                                     target_kind=args.look_for,
                                                                                     test_fold=args.test_fold,
                                                                                     train=False,
                                                                                     print_timing=False,
                                                                                     transform_type='none',
                                                                                     DX=args.dx,
                                                                                     get_images=args.images)
    else:
        train_dset = datasets.WSI_MIL3_dataset(DataSet=args.dataset,
                                               tile_size=TILE_SIZE,
                                               bag_size=TILES_PER_BAG,
                                               target_kind=args.look_for,
                                               TPS=10,
                                               test_fold=args.test_fold,
                                               train=True,
                                               print_timing=args.time,
                                               transform=args.transformation,
                                               DX=args.dx)

        test_dset = datasets.WSI_MIL3_dataset(DataSet=args.dataset,
                                              tile_size=TILE_SIZE,
                                              bag_size=TILES_PER_BAG,
                                              target_kind=args.look_for,
                                              TPS=10,
                                              test_fold=args.test_fold,
                                              train=False,
                                              print_timing=False,
                                              transform=False,
                                              DX=args.dx)

    train_loader = DataLoader(train_dset, batch_size=1, shuffle=True, num_workers=cpu_available, pin_memory=True)
    test_loader  = DataLoader(test_dset, batch_size=1, shuffle=False, num_workers=cpu_available, pin_memory=True)

    # Save transformation data to 'run_data.xlsx'
    transformation_string = ', '.join([str(train_dset.transform.transforms[i]) for i in range(len(train_dset.transform.transforms))])
    utils.run_data(experiment=experiment, transformation_string=transformation_string)

    # Load model
    print()
    model = ResNet50_GN_GatedAttention()
    print()

    '''
    counter = 0
    for module in model.modules():
        if isinstance(module, nn.BatchNorm2d):
            setattr(model, module, nn.GroupNorm(num_groups=32, num_channels=module.num_features, affine=module.affine))
            #module = nn.GroupNorm(num_groups=32, num_channels=module.num_features, affine=module.affine)
            counter += 1
            #module.momentum = 0.01
            # module.track_running_stats = False
    print('Updated {} model variables'.format(counter))
    '''
    utils.run_data(experiment=experiment, model=model.model_name)

    epoch = args.epochs
    from_epoch = args.from_epoch

    # In case we continue from an already trained model, than load the previous model and optimizer data:
    if args.experiment is not 0:
        print('Loading pre-saved model...')
        model_data_loaded = torch.load(os.path.join(args.output_dir,
                                                    'Model_CheckPoints',
                                                    'model_data_Epoch_' + str(args.from_epoch) + '.pt'), map_location='cpu')

        model.load_state_dict(model_data_loaded['model_state_dict'])

        from_epoch = args.from_epoch + 1
        print()
        print('Resuming training of Experiment {} from Epoch {}'.format(args.experiment, args.from_epoch))

    optimizer = optim.Adam(model.parameters(), lr=1e-5, weight_decay=5e-5)

    if DEVICE.type == 'cuda':
        cudnn.benchmark = True

    if args.experiment is not 0:
        optimizer.load_state_dict(model_data_loaded['optimizer_state_dict'])
        for state in optimizer.state.values():
            for k, v in state.items():
                if torch.is_tensor(v):
                    state[k] = v.to(DEVICE)

    # simple_train(model, train_loader, test_loader)
    train(model, train_loader, test_loader, DEVICE=DEVICE, optimizer=optimizer, print_timing=args.time)
