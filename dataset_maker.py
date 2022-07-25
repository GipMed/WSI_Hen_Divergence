from Dataset_Maker import slide_walker, slide_rename, folds_split_per_patient
from Dataset_Maker import slide_remove, hospital_metadata_reader, slide_inspector
import argparse
from utils import get_cpu
import send_gmail
import utils_data_managment
import os

parser = argparse.ArgumentParser(description='Data preparation script')
parser.add_argument('--step', type=int, help='dataset maker step (0-3')
parser.add_argument('--data_dir', type=str, help='location of data root folder')
parser.add_argument('--Dataset', type=str, help='name of dataset')
parser.add_argument('--get_slide_labels', action='store_true', help='collect slide labels')
parser.add_argument('--scan_barcodes', action='store_true', help='try to scan barcodes from the slides label images')
parser.add_argument('--tile_size', type=int, default=256, help='size of tiles')
parser.add_argument('--tissue_coverage', type=float, default=0.3, help='min. tissue % for a valid tile')
parser.add_argument('--mag', type=int, default=10, help='desired magnification of patches')
parser.add_argument('--control_tissue', action='store_true', help='collect slide labels')
parser.add_argument('--reorder_rename_slides', action='store_true', help='rename slides according to barcodes')

args = parser.parse_args()
num_workers = get_cpu()


def prepare_dataset_for_training(Dataset, data_dir, scan_barcodes, get_slide_labels, step, tile_size,
                                 mag, tissue_coverage, is_w_control_tissue, fold_params, reorder_rename_slides):
    if step == 0:
        prepare_dataset_step0(data_dir, Dataset, scan_barcodes, get_slide_labels)

    elif step == 1:
        prepare_dataset_step1(data_dir, Dataset, tile_size, tissue_coverage, mag, is_w_control_tissue, reorder_rename_slides)

    elif step == 2:
        prepare_dataset_step2(data_dir, Dataset, tile_size, tissue_coverage, mag, is_w_control_tissue)

    elif step == 3:
        prepare_dataset_step3(data_dir, Dataset, hospital_metadata_file, fold_params, split_all_dataset_group, binary_label_list)


def prepare_dataset_step0(data_dir, Dataset, scan_barcodes, get_slide_labels):
    slide_walker.create_slide_list(data_dir, Dataset)

    if get_slide_labels:
        slide_walker.add_barcodes_to_slide_list(data_dir, Dataset, scan_barcodes)


def prepare_dataset_step1(data_dir, Dataset, tile_size, tissue_coverage, mag, is_w_control_tissue, reorder_rename_slides):
    slide_walker.merge_manual_barcodes_to_barcode_list(data_dir, Dataset)

    if reorder_rename_slides:
        slide_rename.add_slide_rename_to_barcode_list(data_dir, Dataset)

        slide_rename.rename_slides_according_to_list(data_dir, Dataset)

        slide_rename.delete_empty_folders(data_dir, Dataset)

    utils_data_managment.make_slides_xl_file(DataSet=Dataset, ROOT_DIR=data_dir, out_path=data_dir)

    utils_data_managment.determine_manipulated_objective_power(DataSet=Dataset, ROOT_DIR=data_dir)

    utils_data_managment.make_segmentations(DataSet=Dataset,
                                            ROOT_DIR=data_dir,
                                            out_path=data_dir,
                                            num_workers=num_workers)

    utils_data_managment.make_grid(DataSet=Dataset,
                                   ROOT_DIR=data_dir,
                                   tile_sz=tile_size,
                                   tissue_coverage=tissue_coverage,
                                   desired_magnification=mag,
                                   num_workers=num_workers)

    if is_w_control_tissue:
        slide_inspector.create_slide_inspection_folder(in_dir=os.path.join(data_dir, Dataset),
                                                       out_dir=os.path.join(data_dir, 'thumbs_w_control_tissue'),
                                                       mag=mag,
                                                       thumbs_only=True)


def prepare_dataset_step2(data_dir, Dataset, tile_size, tissue_coverage, mag, is_w_control_tissue):
    if is_w_control_tissue:
        utils_data_managment.make_segmentations(DataSet=Dataset,
                                                ROOT_DIR=data_dir,
                                                out_path=data_dir,
                                                num_workers=num_workers)

        utils_data_managment.make_grid(DataSet=Dataset,
                                       ROOT_DIR=data_dir,
                                       tile_sz=tile_size,
                                       tissue_coverage=tissue_coverage,
                                       desired_magnification=mag,
                                       num_workers=num_workers)

    slide_inspector.create_slide_inspection_folder(in_dir=os.path.join(data_dir, Dataset),
                                                   out_dir=os.path.join(data_dir, 'thumbs'),
                                                   mag=mag,
                                                   thumbs_only=False)


def prepare_dataset_step3(data_dir, Dataset, hospital_metadata_file, fold_params, split_all_dataset_group, binary_label_list=[]):

    slide_remove.remove_slides_according_to_list(data_dir, Dataset)

    rename_duplicate_slides() #TODO

    hospital_metadata_reader.add_hospital_labels_to_metadata(data_dir, Dataset, hospital_metadata_file)

    hospital_metadata_reader.binarize_labels(binary_label_list)

    folds_split_per_patient.split_dataset_into_folds(data_dir, Dataset, fold_params, split_all_dataset_group)


if __name__ == '__main__':
    step = args.step
    data_dir = args.data_dir
    Dataset = args.Dataset
    scan_barcodes = args.scan_barcodes
    get_slide_labels = args.get_slide_labels
    prepare_dataset_for_training(Dataset, data_dir, scan_barcodes, get_slide_labels, step, args.tile_size,
                                 args.mag, args.tissue_coverage, args.control_tissue, args.reorder_rename_slides)
    send_gmail.send_gmail(0, send_gmail.Mode.DATAMAKER)

