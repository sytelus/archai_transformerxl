# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from typing import Optional
import torch
from archai import cifar10_models
from archai.common.trainer import Trainer
from archai.common.config import Config
from archai.common.common import common_init
from archai.datasets import data

def train_test(conf_eval:Config):
    conf_loader       = conf_eval['loader']
    conf_trainer = conf_eval['trainer']

    # create model
    Net = cifar10_models.resnet34
    model = Net().to(torch.device('cuda', 0))

    # get data
    train_dl, _, test_dl = data.get_data(conf_loader)

    # train!
    trainer = Trainer(conf_trainer, model, None)
    trainer.fit(train_dl, test_dl)


if __name__ == '__main__':
    conf = common_init(config_filepath='confs/algos/resnet.yaml')
    conf_eval = conf['nas']['eval']

    train_test(conf_eval)


