# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Extracts Pareto-frontier through Evolutionary Search, given constraints. 
"""

import argparse
import os
import random
from datetime import datetime

import numpy as np
import torch
import yaml

from archai.common import utils


def parse_args():
    parser = argparse.ArgumentParser(description='Characterizing the search space of transformer-based autoregressive language models .')

    try:
        save_path = os.environ['AMLT_OUTPUT_DIR']
    except:
        save_path = '~/logdir' 

    search = parser.add_argument_group('Search configuration')
    search.add_argument('--default_path',
                        type=str,
                        default=save_path,
                        help='Path to the default folder used to save outputs.')

    search.add_argument('--model_type',
                        type=str,
                        default='mem_transformer',
                        choices=['hf_gpt2', 'hf_gpt2_flex', 'hf_transfo_xl', 'mem_transformer'],
                        help='Type of model to be searched.')

    search.add_argument('--model_config',
                        type=str,
                        default=None,
                        help='YAML configuration file to override default configuration.')

    
    search.add_argument('--use_quantization',
                        action='store_true',
                        help='Uses quantized models to measure latency and accuracy.')

    search.add_argument('--seed',
                        type=int,
                        default=1111,
                        help='Random seed.')

    choice = parser.add_argument_group('Hyperparameters choices')
    choice.add_argument('--n_layer',
                        nargs='+',
                        type=int,
                        default=None,
                        help='Choices for number of layers.')

    choice.add_argument('--d_model',
                        nargs='+',
                        type=int,
                        default=None,
                        help='Choices for model dimensions.')

    choice.add_argument('--d_inner',
                        nargs='+',
                        type=int,
                        default=None,
                        help='Choices for inner dimensions.')

    choice.add_argument('--n_head',
                        nargs='+',
                        type=int,
                        default=None,
                        help='Choices for number of attention heads.')

    constraint = parser.add_argument_group('Constraints')
    constraint.add_argument('--constraint_pipeline_type',
                            default='torch',
                            choices=['torch'],
                            help='Type of constraint pipeline to be used during search.')

    constraint.add_argument('--param_constraint_lower',
                            type=int,
                            default=5e6,
                            help='Candidates below total parameters will be rejected.')

    constraint.add_argument('--param_constraint_upper',
                            type=int,
                            default=12e6,
                            help='Candidates above total parameters will be rejected.')

    constraint.add_argument('--latency_constraint_upper',
                            type=float,
                            default=None,
                            help='Candidates above latency will be rejected.')

    constraint.add_argument('--n_threads',
                            type=int,
                            default=1,
                            help='Number of inference threads.')

    constraint.add_argument('--latency_repeat',
                            type=int,
                            default=5,
                            help='Number of latency measurements.')

    constraint.add_argument('--device_name',
                            type=str,
                            default='XeonE5-2690',
                            help='Name of device that search is being conducted on.')

    constraint.add_argument('--eps',
                            type=float,
                            default=0.05,
                            help='Value for neighborhood used around the Pareto front.')
                        
    args, _ = parser.parse_known_args()

    return vars(args)



if __name__ == '__main__':
    # Gathers the command line arguments
    args = parse_args()

    # Applies random seeds
    np.random.seed(args['seed'])
    random.seed(args['seed'])
    torch.manual_seed(args['seed'])

    # Initializes the result's path
    now = datetime.now()
    time_str = now.strftime("%d_%m_%Y_%H_%M_%S")
    results_path_str = f'characterize_{args["model_type"]}_lower_param_{args["param_constraint_lower"]/1e6}M_upper_param_{args["param_constraint_upper"]/1e6}M_latency_upper_{args["latency_constraint_upper"]}s_{args["device_name"]}_{time_str}'
    results_path = os.path.join(args['default_path'], results_path_str)
    args['results_path'] = utils.full_path(results_path, create=True)

    # Dumps the search configuration to a YAML file
    with open(os.path.join(args['results_path'], 'search_config.yaml'), 'w') as f:
        yaml.dump(args, f)

    # Loads model configuration file (if provided)
    try:
        with open(args['model_config'], 'r') as f:
            args['model_config'] = yaml.load(f, Loader=yaml.Loader)['train']
    except:
        args['model_config'] = {}

    # Characterize
    characterizer = CharTransSearchSpace(args)
    characterizer.characterize()