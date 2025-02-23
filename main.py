import argparse
from pathlib import Path
import os
import sys

import torch
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.cli import ArgsType, LightningCLI
from pytorch_lightning.loggers.wandb import WandbLogger
from pytorch_lightning.profilers import AdvancedProfiler

from wsi.datasets.datamodules import WsiDataModule  # , LegacyWsiDataModule
from wsi import utils  # noqa: F401
from wsi.wsi_classifier import WsiClassifier

torch.set_float32_matmul_precision("high")

class WsiLightningCLI(LightningCLI):
    def add_arguments_to_parser(self, parser):
        parser.link_arguments("data.batch_size", "model.batch_size")
        # parser.link_arguments("data.datasets_folds", "model.datasets_folds")
        # parser.link_arguments("data.datasets_folds_val", "model.datasets_folds_val")
        # allow specifying wandb checkpoint paths in the form of "USER/PROJECT/MODEL-RUN_ID:VERSION"
        # reference can be retrieved in artifacts panel
        # "VERSION" can be a version (ex: "v2") or an alias ("latest or "best_k")
        # the file is downloaded to "./artifacts/model-RUN_ID:VERSION/model.ckpt"
        parser.add_argument("--wandb_ckpt_path", type=str)
        # parser.add_argument("--data.datasets_folds", type=dict)

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
        "max_epochs": 1000,
        "callbacks": [lr_monitor],
        "strategy": "ddp_find_unused_parameters_false", 
        # "profiler": AdvancedProfiler(), 
    }

    # note the current run's generated config.yaml file is saved in the cwd and not logged to wandb atm, it is overwritten every run
    # follow https://github.com/Lightning-AI/lightning/issues/14188 for the fix
    cli = WsiLightningCLI(  # noqa: F841
        WsiClassifier,
        WsiDataModule,
        trainer_defaults=trainer_defaults,
        seed_everything_default=True,
        parser_kwargs={
            "fit": {"default_config_files": ["configs/classifier/default_config_fit.yaml"]},
            "test": {"default_config_files": ["configs/classifier/default_config_test.yaml"]},
            "predict": {
                "default_config_files": ["configs/classifier/default_config_predict.yaml"]
            },
        },
        save_config_kwargs={"overwrite": True},
        args=args,
    )


if __name__ == "__main__":
    cli_main()
