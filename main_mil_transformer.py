import argparse
from pathlib import Path
import sys

import torch
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.cli import ArgsType, LightningCLI
from pytorch_lightning.loggers.wandb import WandbLogger

from wsi.datasets.mil_transformer_datamodules import WsiGridFeaturesDataModule, WsiGridDataModule, WsiRandomFeaturesDataModule, WsiMultiGridFeaturesDataModule
from wsi.mil_transformer_classifier import MilTransformerClassifier


class WsiLightningCLI(LightningCLI):
    def add_arguments_to_parser(self, parser):
        parser.link_arguments("data.batch_size", "model.batch_size")
        parser.link_arguments("data.bag_size", "model.bag_size")
        # allow specifying wandb checkpoint paths in the form of "USER/PROJECT/MODEL-RUN_ID:VERSION"
        # reference can be retrieved in artifacts panel
        # "VERSION" can be a version (ex: "v2") or an alias ("latest or "best_k")
        # the file is downloaded to "./artifacts/model-RUN_ID:VERSION/model.ckpt"
        parser.add_argument("--wandb_ckpt_path", type=str)

    def before_fit(self):
        wandb_ckpt_path = vars(self.config["fit"]).get("wandb_ckpt_path")
        if wandb_ckpt_path is not None:
            artifact_path = self.download_wandb_ckpt(wandb_ckpt_path)
            self.config["ckpt_path"] = artifact_path

    def before_test(self):
        wandb_ckpt_path = vars(self.config["test"]).get("wandb_ckpt_path")
        if wandb_ckpt_path is not None:
            self.download_wandb_ckpt(wandb_ckpt_path)

    def before_predict(self):
        wandb_ckpt_path = vars(self.config["predict"]).get("wandb_ckpt_path")
        if wandb_ckpt_path is not None:
            self.download_wandb_ckpt(wandb_ckpt_path)

    @staticmethod
    def download_wandb_ckpt(ckpt_path):
        artifact_dir = WandbLogger.download_artifact(ckpt_path, artifact_type="model")
        artifact_path = str(Path(artifact_dir) / "model.ckpt")
        print(f"Downloaded checkpoint from wandb: {artifact_path}")
        return artifact_path



def cli_main(args: ArgsType = None):
    lr_monitor = LearningRateMonitor()
    trainer_defaults = {
        "accelerator": "gpu" if torch.cuda.is_available() else "cpu",
        "devices": "auto",
        "max_epochs": 100,
        "callbacks": [lr_monitor],
    }

    if patch_sampling=="random":
        DataModule = WsiRandomFeaturesDataModule
    elif patch_sampling=="multi_grid":
        DataModule = WsiMultiGridFeaturesDataModule
    elif patch_sampling=="grid":
        DataModule = WsiGridFeaturesDataModule

    # note the current run's generated config.yaml file is saved in the cwd and not logged to wandb atm, it is overwritten every run
    # follow https://github.com/Lightning-AI/lightning/issues/14188 for the fix
    cli = WsiLightningCLI(  # noqa: F841
        MilTransformerClassifier,
        DataModule,
        # WsiGridDataModule,
        trainer_defaults=trainer_defaults,
        seed_everything_default=True,
        parser_kwargs={
            "fit": {
                "default_config_files": [
                    "configs/mil_transformer/default_config_fit.yaml"
                ]
            },
            "test": {
                "default_config_files": [
                    "configs/mil_transformer/default_config_test.yaml"
                ]
            },
        },
        save_config_kwargs={"overwrite": True},
        args=args,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Top script arguments")
    parser.add_argument('--patch_sampling', type=str, default="none", help='Patch Sampling Strategy')

    # Parse only the known arguments (those for the script)
    args, unknown = parser.parse_known_args()

    # Extract the value of the flag
    patch_sampling = args.patch_sampling
    
    # Now pass the remaining arguments to LightningCLI
    sys.argv = [sys.argv[0]] + unknown
    
    cli_main()
