from typing import List, Union, Dict
import random
from pathlib import Path

import ray

import torch
from torch import nn
from torch.utils.data import DataLoader
import numpy as np
import pytorch_lightning as pl
from archai.algos.evolution_pareto_image_seg.model import SegmentationNasModel
from archai.algos.evolution_pareto_image_seg.face_synthetics_data import FaceSynthetics
import segmentation_models_pytorch as smp


def get_custom_overall_metrics(tp, fp, fn, tn, stage):
    gt_pos = (tp + fn).sum(axis=0)
    pd_pos = (tp + fp).sum(axis=0)

    tp_diag = tp.sum(axis=0)
    f1 = 2 * tp_diag / torch.maximum(torch.ones_like(gt_pos), gt_pos + pd_pos)
    iou = tp_diag / torch.maximum(torch.ones_like(gt_pos), gt_pos + pd_pos - tp_diag)

    weight = 1 / torch.sqrt(gt_pos[1:18])
    overall_f1 = torch.sum(f1[1:18] * weight) / torch.sum(weight)
    overall_iou = torch.sum(iou[1:18] * weight) / torch.sum(weight)

    return {
        f'{stage}_overall_f1': overall_f1,
        f'{stage}_overall_iou': overall_iou
    }


class LightningModelWrapper(pl.LightningModule):
    def __init__(self,
                 model: SegmentationNasModel,
                 criterion_name: str = 'ce',
                 lr: float = 2e-4,
                 lr_exp_decay_gamma: float = 0.98,
                 img_size: int = 256):

        super().__init__()

        self.model = model
        self.lr = lr
        self.lr_exp_decay_gamma = lr_exp_decay_gamma
        self.latency = None
        self.img_size = img_size
        
        self.set_loss(criterion_name)
        self.save_hyperparameters()

    def set_loss(self, criterion_name):
        if criterion_name == 'ce':
            self.loss_fn = smp.losses.SoftCrossEntropyLoss(ignore_index=255, smooth_factor=0)
        elif criterion_name == 'dice':
            self.loss_fn = smp.losses.DiceLoss(smp.losses.MULTICLASS_MODE, from_logits=True, ignore_index=255)
        elif criterion_name == 'lovasz':
            self.loss_fn = smp.losses.LovaszLoss(smp.losses.MULTICLASS_MODE, ignore_index=255, from_logits=True)

    def forward(self, image):
        return self.model(image)

    def evaluate(self, dataloader: DataLoader) -> Dict[str, float]:
        with torch.no_grad():
            outputs = [
                {k: v.cpu() for k, v in self.shared_step(batch.cuda()).items()}
                for batch in dataloader
            ]
            return self.shared_epoch_end(outputs, stage='validation', log=False)

    def shared_step(self, batch):
        image = batch['image']

        assert image.ndim == 4

        h, w = image.shape[2:]
        assert h % 32 == 0 and w % 32 == 0

        mask = batch['mask']
        logits_mask = self.forward(image)
        loss = self.loss_fn(logits_mask, mask)

        pred_classes = logits_mask.argmax(axis=1)

        tp, fp, fn, tn = smp.metrics.get_stats(
            pred_classes, mask.long(), mode='multiclass',
            num_classes=self.model.nb_classes, ignore_index=255
        )

        metrics_result = {
            'tp': tp,
            'fp': fp,
            'fn': fn,
            'tn': tn,
            'loss': loss
        }

        return metrics_result

    def training_step(self, batch, batch_idx):
        results = self.shared_step(batch)
        self.log_dict({'training_loss': results['loss']}, sync_dist=True)

        return results

    def predict(self, image):
        with torch.no_grad():
            return self.model.predict(image)

    def validation_step(self, batch, batch_idx):
        results = self.shared_step(batch)
        return results

    def validation_epoch_end(self, outputs):
        self.shared_epoch_end(outputs, stage='validation')

    def training_epoch_end(self, outputs):
        self.shared_epoch_end(outputs, stage='train')

    def shared_epoch_end(self, outputs, stage, log=True):
        tp = torch.cat([x['tp'] for x in outputs])
        fp = torch.cat([x['fp'] for x in outputs])
        fn = torch.cat([x['fn'] for x in outputs])
        tn = torch.cat([x['tn'] for x in outputs])
        avg_loss = torch.tensor([x['loss'] for x in outputs]).mean()

        results = get_custom_overall_metrics(tp, fp, fn, tn, stage=stage)
        results[f'{stage}_loss'] = avg_loss

        if log:
            self.log_dict(results, sync_dist=True)

        return results

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.lr)
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=self.lr_exp_decay_gamma)

        scheduler = {
            'scheduler': scheduler,
            'interval': 'epoch'
        }

        return [optimizer], [scheduler]

    def on_train_start(self) -> None:
        sample = torch.randn((1, 3, self.img_size, self.img_size)).to(self.device)
        self.logger.experiment.add_graph(self.model, sample)


class SegmentationTrainer():

    def __init__(self, model: SegmentationNasModel, dataset_dir: str,
                 max_steps: int = 12000, val_size: int = 2000,
                 img_size: int = 256,
                 augmentation: str = 'none', batch_size: int = 16,
                 lr: float = 2e-4, criterion_name: str = 'ce', 
                 val_check_interval: Union[int, float] = 0.25, 
                 lr_exp_decay_gamma: float = 0.98,
                 seed: int = 1):
        torch.manual_seed(seed)
        random.seed(seed)
        np.random.seed(int(seed))

        self.max_steps = max_steps
        self.val_check_interval = val_check_interval
        self.data_dir = Path(dataset_dir)
        self.tr_dataset = FaceSynthetics(self.data_dir, subset='train', val_size=val_size,
                                         img_size=(img_size, img_size), augmentation=augmentation)
        self.val_dataset = FaceSynthetics(self.data_dir, subset='validation', val_size=val_size,
                                          img_size=(img_size, img_size), augmentation=augmentation)

        self.tr_dataloader = DataLoader(self.tr_dataset, batch_size=batch_size, num_workers=8, shuffle=True)
        self.val_dataloader = DataLoader(self.val_dataset, batch_size=batch_size, num_workers=8, shuffle=False)

        self.model = LightningModelWrapper(model, criterion_name=criterion_name, lr=lr,
                                           img_size=img_size, lr_exp_decay_gamma=lr_exp_decay_gamma)
        self.img_size = img_size

    def get_training_callbacks(self, run_dir: Path) -> List[pl.callbacks.Callback]:
        return [pl.callbacks.ModelCheckpoint(
            dirpath=str(run_dir / 'best_model'),
            mode='max', save_top_k=1, verbose=True,
            monitor='validation_overall_f1',
            filename='{epoch}-{step}-{validation_overall_f1:.2f}'
        ), pl.callbacks.lr_monitor.LearningRateMonitor()]

    def fit(self, run_path: str) -> pl.Trainer:
        run_path = Path(run_path)
        arch = self.model.model

        # Saves architecture metadata
        arch.to_file(run_path / 'architecture.yaml')

        # Saves architecture diagram
        digraph = arch.view()
        digraph.render(str(run_path / 'architecture'), format='png')

        trainer = pl.Trainer(
            max_steps=self.max_steps,
            default_root_dir=run_path,
            callbacks=self.get_training_callbacks(run_path),
            val_check_interval=self.val_check_interval,
            gpus=1
        )

        trainer.fit(self.model, self.tr_dataloader, self.val_dataloader)
        trainer.save_checkpoint(str(run_path / 'model.ckpt'))
        return trainer

    def fit_and_validate(self, run_path: str)->float:
        trainer = self.fit(run_path)
        metrics = trainer.validate(model=trainer.model, dataloaders=self.val_dataloader)[0]
        return metrics['validation_overall_f1']
