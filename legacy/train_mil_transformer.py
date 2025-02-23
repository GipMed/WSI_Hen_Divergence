import os
from argparse import ArgumentParser

import pytorch_lightning as pl
import torch
from mil_transformer_datamodules import WsiMilDataModule, WsiMilFeaturesDataModule
from mil_transformer_module import MilTransformerModule
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.callbacks.model_checkpoint import ModelCheckpoint
from pytorch_lightning.loggers.tensorboard import TensorBoardLogger
from pytorch_lightning.loggers.wandb import WandbLogger
from pytorch_lightning.profilers import SimpleProfiler
from utils_MIL import get_RegModel_Features_location_dict

from models import preact_resnet


def cli_main():
    parser = ArgumentParser()

    # program args
    parser.add_argument(
        "--seed", type=int, default=None, help="seed for initializing training. "
    )
    parser.add_argument(
        "--ckpt_path",
        default=None,
        help="load training state from lightning checkpoint",
    )
    parser.add_argument(
        "--user",
        default=None,
        help="wandb checkpoint from another user who is not tal",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=0,
        help="early stopping patience in epochs, set as 0 for no early stopping",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="only validate  on test features using checkpoint given by ckpt_path and features from test_features_dir",
    )
    parser.add_argument(
        "--logdir_postfix",
        type=str,
        default="",
        help="add a postfix to the tensorboard experiment log dir name, useful for naming experiments",
    )
    parser.add_argument(
        "--feature_extractor_ckpt",
        default="",
        help="path of override feature extractor checkpoint when training with patches, if empty then the default feature extractor for the dataset and fold will be used",
    )
    parser.add_argument(
        "--no_wandb",
        action="store_true",
        help="Use tensorboard instead of wandb for logging",
    )
    parser.add_argument(
        "--exp_name", type=str, default=None, help="Display name for WandB experiment"
    )
    parser.add_argument(
        "--auto_find_batch_size",
        action="store_true",
        help="automatically find largest batch size that fits in memory",
    )
    parser.add_argument(
        "--log_parameters",
        action="store_true",
        help="log model parameters and gradients to wandb",
    )

    # dataset args
    parser.add_argument(
        "--dataset", type=str, default="CAT", help="dataset to train on"
    )
    parser.add_argument(
        '--batch', type=int, default=9, help='Batch No. for carmel test dataset (9, 10, 11)'
    )
    parser.add_argument(
        "--dataset_type",
        type=str,
        default="features",
        choices=["features", "patches"],
        help="use a pretrained feature extractor to generate features from patches, or read features from feature files",
    )
    parser.add_argument("--val_fold", type=int, default=1, help="validation fold index")
    parser.add_argument("--cross_validation", action="store_true")
    parser.add_argument("-tar", "--target", type=str, default="ER", help="target label")
    parser.add_argument(
        "--bag_size", type=int, default=100, help="# of patches per MIL bag"
    )
    parser.add_argument("--num_workers", type=int, default=8)
    parser.add_argument(
        "--batch_size", type=int, default=32, help="number of bags per batch"
    )
    parser.add_argument("--profile", action="store_true", help="profile training")
    parser.add_argument(
        "--train_features_dir",
        type=str,
        default="",
        help="override directory to fetch train features from",
    )
    parser.add_argument(
        "--test_features_dir",
        type=str,
        default="",
        help="Directory to fetch test features from, if empty then validation fold will be used for test",
    )
    parser.add_argument(
        "--test_dataset",
        type=str,
        default="",
        help="dataset name for metadata when testing",
    )
    parser.add_argument(
        "--no_val", action="store_true", help="do not perform validation"
    )
    # parser.add_argument('--test_dataset',
    #                     type=str,
    #                     default='',
    #                     help='test dataset if testing with feature extractor')

    # trainer args
    parser = Trainer.add_argparse_args(parser)

    # model args
    parser = MilTransformerModule.add_model_specific_args(parser)

    args = parser.parse_args()

    if args.seed is not None:
        pl.seed_everything(args.seed, workers=True)
        # Ensure that all operations are deterministic on GPU (if used) for reproducibility
        torch.backends.cudnn.determinstic = True
        torch.backends.cudnn.benchmark = False

    # Cross validation, currently doesn't do much due to feature data files only existing for a single fold, so every fold would be a new experiment anyway
    # if args.cross_validation:
    #     folds = list(range(1, 6))
    # else:
    #     folds = [args.val_fold]
    #
    # for val_fold in folds:
    #     args.val_fold = val_fold

    if args.test_features_dir != "" and (args.test or args.train_features_dir != ""):
        # override both train and test features dir or only test
        data_location = {
            "TrainSet Location": args.train_features_dir,
            "TestSet Location": args.test_features_dir,
        }
    elif args.train_features_dir != "":
        # override train features dir only for full training without val or test
        data_location = {
            "TrainSet Location": args.train_features_dir,
            "TestSet Location": "",
        }
    else:
        # use default data location (usually in regular training)
        try:
            data_location = get_RegModel_Features_location_dict(
                train_DataSet=args.dataset, target=args.target, test_fold=args.val_fold
            )
        except KeyError:
            data_location = None
            print(
                "Could not find features data location in dict, ignore this if extracting features dynamically"
            )

    # init datamodule
    if args.dataset_type == "patches":
        dm = WsiMilDataModule.from_argparse_args(args)
    else:
        dm = WsiMilFeaturesDataModule.from_argparse_args(
            args, data_location=data_location
        )

    # load pretrained feature extractor if needed
    if args.dataset_type == "patches":
        feature_extractor_model = preact_resnet.PreActResNet50_Ron(
            train_classifier_only=True
        )
        # if args.model_type == 'preact':
        # feature_extractor_model.linear = Identity()
        # elif args.model_type == 'resnet':
        #     feature_extractor_model.fc = Identity()
        # else:
        #     print('Unknown model type')
        #     return
        model_data_loaded = torch.load(
            data_location["REG Model Location"]
            if data_location is not None
            else args.feature_extractor_ckpt
        )
        feature_extractor_model.load_state_dict(model_data_loaded["model_state_dict"])

        # update feature dim
        _, features = feature_extractor_model(torch.randn(1, 3, 256, 256))
        args.input_dim = features.squeeze().shape[0]
        print(f"Computed feature dim: {args.input_dim}")
        assert args.input_dim == features.numel(), "Features should be flattened"
    else:
        feature_extractor_model = None

    # init model
    model = MilTransformerModule(
        **args.__dict__, feature_extractor_model=feature_extractor_model
    )

    # logger
    if args.no_wandb:
        logger = TensorBoardLogger(
            save_dir="lightning_logs/MilTransformer",
            name="-".join(
                filter(
                    None,
                    [
                        args.dataset,
                        args.target,
                        f"fold{args.val_fold}",
                        args.logdir_postfix,
                    ],
                )
            ),
        )
    else:
        logger = WandbLogger(
            project="MIL-Transformer", name=args.exp_name, log_model=True
        )
        if args.log_parameters:
            logger.watch(model, log="all", log_freq=500, log_graph=False)

    # callbacks
    checkpoint_callback = ModelCheckpoint(
        monitor="val_auc",
        dirpath=(
            os.path.join("./checkpoints/wandb", logger.experiment.id)
            if not args.no_wandb
            else None
        ),
        filename="{epoch}-{val_auc:.3f}",
        save_top_k=3,
        mode="max",
        save_last=True,
        auto_insert_metric_name=True,
    )
    early_stopping_callback = EarlyStopping(
        monitor="val_auc", patience=args.patience, mode="max"
    )
    lr_monitor = LearningRateMonitor(logging_interval="step")
    callbacks = [checkpoint_callback, lr_monitor]
    if args.patience > 0:
        callbacks.append(early_stopping_callback)

    trainer = Trainer.from_argparse_args(
        args,
        accelerator="gpu" if torch.cuda.is_available() else "cpu",
        devices="auto",
        max_epochs=100 if args.max_epochs is None else args.max_epochs,
        logger=logger,
        num_sanity_val_steps=5,
        callbacks=callbacks,
        limit_val_batches=(0.0 if args.no_val else None),
        profiler=(SimpleProfiler(filename="profiler_output") if args.profile else None),
        auto_scale_batch_size=args.auto_find_batch_size,
    )

    # find batch size
    if args.auto_find_batch_size:
        trainer.tune(model, datamodule=dm)

    ckpt_path = args.ckpt_path
    if ckpt_path is not None and ckpt_path.startswith("wandb"):
        if args.user is not None:
            user = args.user
        else:
            user = "talneoran"
        run_id, run_version = args.ckpt_path.split(":")[1:]
        checkpoint_reference = f"{user}/MIL-Transformer/model-{run_id}:{run_version}"
        # download checkpoint locally (if not already cached)
        if args.no_wandb:
            artifact_dir = WandbLogger.download_artifact(
                artifact=checkpoint_reference, artifact_type="model"
            )
        else:
            try:
                artifact_dir = logger.download_artifact(
                    artifact=checkpoint_reference, artifact_type="model"
                )
            except:
                artifact_dir = logger.experiment.use_artifact(checkpoint_reference, "model").download()
                
        ckpt_path = os.path.join(artifact_dir, "model.ckpt")

    # only test with given checkpoint on test_features_dir
    if args.test:
        trainer.test(model, ckpt_path=ckpt_path, datamodule=dm)
        return

    # train
    trainer.fit(model, datamodule=dm, ckpt_path=ckpt_path)

    # test with best checkpoint after validation
    if not args.no_val:
        trainer.test(model, ckpt_path="best", datamodule=dm)


if __name__ == "__main__":
    cli_main()
