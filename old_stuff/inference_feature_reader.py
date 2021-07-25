import pickle
import os

dirname = r'C:\Pathnet_results\MIL_general_try4\ABCTB_TCGA_runs\Her2\exp308\Inference\features'
fname = 'Model_Epoch_1000-Folds_[1]_Her2-Tiles_500_features_slides_700.data'
#fname = 'Model_Epoch_1000-Folds_[2, 3, 4, 5]_Her2-Tiles_500_features_slides_3000.data'

with open(os.path.join(dirname, fname), 'rb') as filehandle:
    inference_data = pickle.load(filehandle)

labels, targets, scores, patch_scores, slide_names, features = inference_data

print('file successfully read')

