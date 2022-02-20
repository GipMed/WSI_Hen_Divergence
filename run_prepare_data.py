import subprocess

subprocess.run(['python', 'prepare_data.py',
                '--segmentation',
                #'--hard_copy',
                #'--data_collection',
                '--grid',
                #'--data_root', r'C:\ran_data\TCGA_example_slides\TCGA_examples_131020_flat',
                #'--data_root', r'C:\ran_data\TCGA_example_slides\bad_seg_230221',
                #'--data_root', r'C:\ran_data\ABCTB\ABCTB_examples',
                #'--data_root', r'C:\ran_data\IHC_examples',
                #'--data_root', r'C:\ran_data',
                '--data_root', r'C:\ran_data\TMA\02-008',
                #'--data_root', r'C:\temp\roy',
                #'--data_root', r'C:\ran_data\Benign\batch1',
                #'--data_folder', r'C:\ran_data\TCGA_example_slides\TCGA_bad_examples_181020',
                #'--data_root', r'C:\ran_data\Lung_examples',
                #'--data_root', r'C:\ran_data\herohe_grid_debug_temp_060121',
                #'--data_root', r'C:\ran_data\RedSquares',
                #'--data_root', r'C:\ran_data\Carmel_Slides_examples',
                #'--data_root', r'C:\ran_data\HEROHE_examples',
                #'--dataset','HEROHE',
                #'--dataset','CARMEL3',
                #'--dataset', 'TCGA',
                #'--dataset', 'ABCTB',
                #'--dataset', 'ABCTB_TIF',
                '--dataset', 'TMA',
                #'--dataset', 'BENIGN1',
                #'--dataset', 'PORTO_HE',
                #'--tissue_coverage', '0.3',
                #'--tissue_coverage', '0.1',
                '--mag', '7',
                '--tile_size', '512',
                #'--oversized_HC_tiles'
                #'--data_folder', r'C:\ran_data\gip-main_examples\Leukemia',
                #'--data_folder', r'C:\ran_data\ABCTB',
                #'--as_jpg',
                #'--added_extension', '_tlsz1000_mag10',
                ])