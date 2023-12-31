# Copyright (c) 2019-2020, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import logging
import math
import os
import pickle
import sys
import time
import warnings

import dllogger
import numpy as np
import torch
import yaml
try:
    import pyprof
except ModuleNotFoundError:
    warnings.warn('PyProf is unavailable')

from archai.nlp.nvidia_transformer_xl import data_utils
from archai.nlp.nvidia_transformer_xl import nvidia_utils
from archai.nlp.nvidia_transformer_xl.nvidia_utils import exp_utils
from archai.nlp.nvidia_transformer_xl.data_utils import get_lm_corpus
from archai.nlp.nvidia_transformer_xl.data_utils import tokenize_raw
from archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils import AverageMeter
from archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils import benchmark
from archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils import create_exp_dir
from archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils import l2_promote
from archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils import log_env_info


def parse_args():
    parent_parser = argparse.ArgumentParser(
        description='PyTorch Transformer-XL Language Model',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
        )

    parser = argparse.ArgumentParser(parents=[parent_parser], add_help=True)
    cfg_parser = argparse.ArgumentParser(parents=[parent_parser], add_help=False)

    cfg_parser.add_argument('--config', default='default')
    cfg_parser.add_argument('--config_file', default=None)

    config_args, _ = cfg_parser.parse_known_args()

    if config_args.config is not None and config_args.config_file is not None:
        with open(config_args.config_file) as f:
            config = yaml.load(f, Loader=yaml.FullLoader)[config_args.config]['eval']
    else:
        config = {}

    parser.add_argument('--work_dir', default='~/logdir', type=str,
                         help='Directory for the results')
    parser.add_argument('--debug', action='store_true',
                        help='run in debug mode (do not create exp dir)')
    parser.add_argument('--data', type=str, default=None,
                         help='Location of the data corpus')
    parser.add_argument('--cache_dir', default=None, type=str,
                         help='Directory to store dataset cache, if None then use data dir as parent')

    parser.add_argument('--manual', type=str, default=None, nargs='+',
                        help='run model on raw input data')
    parser.add_argument('--dataset', type=str, default='wt103',
                        choices=['wt103', 'lm1b', 'enwik8', 'text8', 'olx'],
                        help='dataset name')
    parser.add_argument('--split', type=str, default='all',
                        choices=['all', 'valid', 'test'],
                        help='which split to evaluate')
    parser.add_argument('--affinity', type=str,
                        default='single_unique',
                        choices=['socket', 'single', 'single_unique',
                                 'socket_unique_interleaved',
                                 'socket_unique_continuous',
                                 'disabled'],
                        help='type of CPU affinity')
    parser.add_argument('--profile', action='store_true',
                        help='Enable profiling with DLProf')
    parser.add_argument('--type', type=str, default='pytorch',
                        choices=['pytorch', 'torchscript'],
                        help='type of runtime to use')
    parser.add_argument('--batch_size', type=int, default=16,
                        help='batch size')
    parser.add_argument('--tgt_len', type=int, default=64,
                        help='number of tokens to predict')
    parser.add_argument('--ext_len', type=int, default=0,
                        help='length of the extended context')
    parser.add_argument('--mem_len', type=int, default=640,
                        help='length of the retained previous heads')
    parser.add_argument('--seed', type=int, default=1111,
                        help='Random seed')
    parser.add_argument('--clamp_len', type=int, default=-1,
                        help='max positional embedding index')
    parser.add_argument('--cuda', action='store_true',
                        help='Run evaluation on a GPU using CUDA')
    parser.add_argument('--model', type=str, default='',
                        help='path to the checkpoint')
    parser.add_argument('--manual_config', type=json.loads, default=None,
                        help='Manually specify config for the model')
    parser.add_argument('--manual_vocab', type=str, default='word',
                        choices=['word', 'bbpe', 'gpt2'],
                        help='Manually specify type of vocabulary')
    parser.add_argument('--fp16', action='store_true',
                        help='Run training in fp16/mixed precision')
    parser.add_argument('--log_all_ranks', action='store_true',
                        help='Enable logging for all distributed ranks')
    parser.add_argument('--dllog_file', type=str, default='eval_log.json',
                        help='Name of the DLLogger output file')
    parser.add_argument('--same_length', action='store_true',
                        help='set same length attention with masking')
    parser.add_argument('--no_env', action='store_true',
                        help='Do not print info on execution env')
    parser.add_argument('--log_interval', type=int, default=10,
                        help='Report interval')
    parser.add_argument('--target_perplexity', type=float, default=None,
                        help='target perplexity')
    parser.add_argument('--target_throughput', type=float, default=None,
                        help='target throughput')
    parser.add_argument('--save_data', action='store_true',
                        help='save latency and throughput data to a file')
    parser.add_argument('--repeat', type=int, default=1,
                        help='loop over the dataset REPEAT times')
    parser.add_argument('--max_size', type=int, default=None,
                        help='run inference on up to MAX_SIZE batches')
    parser.add_argument('--percentiles', nargs='+', default=[90, 95, 99],
                        help='percentiles for latency confidence intervals')
    parser.add_argument('--save_torchscript', default=None, type=str,
                        help='save torchscript model to a file')
    parser.add_argument('--load_torchscript', default=None, type=str,
                        help='load torchscript model from a file')
    parser.add_argument('--local_rank',  type=int,
                        default=os.getenv('LOCAL_RANK', 0),
                        help='Used for multi-process training.')

    parser.set_defaults(**config)
    args, _ = parser.parse_known_args()

    if args.manual:
        args.batch_size = 1

    if args.same_length and args.tgt_len > args.mem_len:
        warnings.warn('--same_length is intended to be used with large '
                      'mem_len relative to tgt_len')

    if args.ext_len < 0:
        raise RuntimeError('Extended context length must be non-negative')
    return args


def load_checkpoint(path):
    dst = f'cuda:{torch.cuda.current_device()}'
    logging.info(f'Loading checkpoint from {path}')
    checkpoint = torch.load(path, map_location=dst)
    return checkpoint


def format_log(loss, split, args):
    if args.dataset in ['enwik8', 'text8']:
        log_str = '| {0} loss {1:5.2f} | {0} bpc {2:9.5f} '.format(
            split, loss, loss / math.log(2))
    else:
        log_str = '| {0} loss {1:5.2f} | {0} ppl {2:9.3f} '.format(
            split, loss, math.exp(loss))
    return log_str


def evaluate(eval_iter, model, meters, log_interval, max_size=None, repeat=1):
    total_len, total_loss = 0, 0.
    eval_step = 0

    log_throughput = 0
    log_latency = 0
    log_loss = 0

    torch.cuda.synchronize()
    start_time = time.time()
    with torch.no_grad():
        mems = None
        for _ in range(repeat):
            for idx, (data, target, seq_len, warm) in enumerate(eval_iter):
                if max_size and idx >= max_size:
                    break
                eval_step += 1

                torch.cuda.synchronize()
                start_iter = time.time()
                loss, mems, _, _ = model(data, target, mems)
                torch.cuda.synchronize()
                elapsed = time.time() - start_iter

                loss = loss.float().mean()
                log_loss += loss.item()
                if warm:
                    total_loss += seq_len * loss.item()
                    total_len += seq_len

                meters['eval_latency'].update(elapsed)
                log_latency += elapsed

                target_tokens = target.numel()
                throughput = target_tokens / elapsed
                throughput = nvidia_utils.distributed.all_reduce_item(throughput, op='sum')
                meters['eval_throughput'].update(throughput)
                log_throughput += throughput

                if eval_step % log_interval == 0:
                    log_throughput /= log_interval
                    log_latency /= log_interval
                    log_loss /= log_interval
                    log_ppl = math.exp(log_loss)

                    log_str = '| step {:>8d} | batches {:>6d} / {:d} ' \
                        '| ms/batch {:5.2f} | tok/s {:7.0f} | loss {:5.2f} | ppl {:5.2f}'.format(
                            eval_step,
                            idx+1,
                            eval_iter.n_batch,
                            log_latency * 1000,
                            log_throughput,
                            log_loss,
                            log_ppl,
                            )
                    logging.info(log_str)

                    dllogger_data = {
                        'eval_latency': log_latency * 1000,
                        'eval_throughput': log_throughput,
                        'eval_loss': log_loss,
                        'eval_perplexity': log_ppl,
                        }
                    dllogger.log(step=tuple([eval_step]), data=dllogger_data)

                    log_throughput = 0
                    log_latency = 0
                    log_loss = 0

    nvidia_utils.distributed.barrier()
    torch.cuda.synchronize()
    total_time = time.time() - start_time
    logging.info('Time : {:.2f}s, {:.2f}ms/segment'.format(
            total_time, 1000 * total_time / (idx+1)))

    avg_loss = total_loss / total_len
    avg_loss = nvidia_utils.distributed.all_reduce_item(avg_loss, op='mean')
    return avg_loss


def compile_model(model, device, args):
    inp = torch.randint(0, 1000, (args.tgt_len, args.batch_size)).to(device)
    tgt = torch.randint(0, 1000, (args.tgt_len, args.batch_size)).to(device)
    start = time.time()
    with torch.no_grad():
        mems = None
        for _ in range(2):
            _, mems, _, _ = model(inp, tgt, mems)
    torch.cuda.synchronize()
    stop = time.time()
    logging.info(f'Building the model took {stop - start:.2f} seconds')


def main():
    args = parse_args()
    if args.affinity != 'disabled':
        nproc_per_node = torch.cuda.device_count()
        affinity = nvidia_utils.gpu_affinity.set_affinity(
            args.local_rank,
            nproc_per_node,
            args.affinity
        )
        print(f'{args.local_rank}: thread affinity: {affinity}')

    if args.type == 'pytorch':
        from mem_transformer import MemTransformerLM
    else:
        from inference.mem_transformer_jit import MemTransformerLM

    torch.cuda.set_device(args.local_rank)
    l2_promote()
    device = torch.device('cuda' if args.cuda else 'cpu')
    nvidia_utils.distributed.init_distributed(args.cuda)

    args.data, args.work_dir, args.cache_dir, dataroot = \
        exp_utils.get_create_dirs(args.data, args.dataset, args.experiment_name,
                                  args.work_dir, args.cache_dir)

    with nvidia_utils.distributed.sync_workers() as rank:
        if rank == 0:
            create_exp_dir(args.work_dir, debug=args.debug)

    # Setup logging
    if args.log_all_ranks:
        log_file = f'eval_log_rank_{nvidia_utils.distributed.get_rank()}.log'
    else:
        log_file = f'eval_log.log'

    dllog_file = args.dllog_file
    log_file = os.path.join(args.work_dir, log_file)
    dllog_file = os.path.join(args.work_dir, dllog_file)
    if args.debug:
        log_file = os.devnull
        dllog_file = os.devnull

    archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils.setup_logging(log_all_ranks=args.log_all_ranks,
                                  filename=log_file,
                                  filemode='a',
                                  )
    archai.nlp.nvidia_transformer_xl.nvidia_utils.exp_utils.setup_dllogger(enabled=True, filename=dllog_file)

    if args.profile:
        try:
            pyprof.init(enable_function_stack=True)
        except NameError:
            warnings.warn('Called pyprof.init() but pyprof is not available')

    logging.info(args)
    dllogger.log(step='PARAMETER', data=vars(args))

    if not args.no_env:
        log_env_info()

    # Set the random seed manually for reproducibility.
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    if args.model:
        model_path = args.model
    elif args.work_dir:
        model_path = os.path.join(args.work_dir, 'checkpoint_best.pt')
    else:
        raise RuntimeError('Specify path to checkpoint using --model or --work_dir')

    if not args.manual_config:
        checkpoint = load_checkpoint(model_path)
        vocab_type = checkpoint['args'].vocab
    else:
        checkpoint = None
        vocab_type = args.manual_vocab

    if args.manual:
        vocab = checkpoint['vocab']

        if hasattr(vocab, 'sym2idx') and not hasattr(vocab, 'unk_idx'):
            vocab.unk_idx = vocab.sym2idx['<unk>']

        text = " ".join(args.manual)
        tokenized = tokenize_raw(text) # text cleanup and transforms
        tensor = vocab.encode_text(tokenized)

        iter = data_utils.LMOrderedIterator(tensor, bsz=args.batch_size,
                                            bptt=args.tgt_len, device=device,
                                            ext_len=args.ext_len, warmup=False)
    else:
        # Load dataset
        corpus = get_lm_corpus(args.data, args.cachedir, args.dataset, vocab_type)

        if args.split == 'valid' or args.split == 'test':
            iter = corpus.get_iterator(args.split, args.batch_size, args.tgt_len,
                                       device=device, mem_len=args.mem_len,
                                       ext_len=args.ext_len)
        else:
            raise RuntimeError('Unknown split')

    if args.fp16:
        dtype = torch.float16
        math_str = 'fp16'
    else:
        dtype = torch.float32
        math_str = 'fp32'

    if args.load_torchscript:
        model = torch.jit.load(args.load_torchscript)
    elif not args.manual_config:
        checkpoint['model_config']['tgt_len'] = args.tgt_len
        checkpoint['model_config']['ext_len'] = args.ext_len
        checkpoint['model_config']['mem_len'] = args.mem_len
        checkpoint['model_config']['clamp_len'] = args.clamp_len
        checkpoint['model_config']['same_length'] = args.same_length
        checkpoint['model_config']['dtype'] = dtype

        model = MemTransformerLM(**checkpoint['model_config'])
        if args.type == 'pytorch':
            model.load_state_dict(checkpoint['model_state'])
        elif args.type == 'torchscript':
            model.load_state_dict(checkpoint['model_state'], strict=False)
    elif args.manual_config:
        args.manual_config['tgt_len'] = args.tgt_len
        args.manual_config['ext_len'] = args.ext_len
        args.manual_config['mem_len'] = args.mem_len
        args.manual_config['clamp_len'] = args.clamp_len
        args.manual_config['same_length'] = args.same_length
        args.manual_config['dtype'] = dtype

        model = MemTransformerLM(**args.manual_config)

    model = model.eval()
    model = model.to(device)
    model = model.to(dtype)

    if args.type == 'torchscript' and not args.manual_config:
        state = checkpoint['model_state']

        tie_projs = checkpoint['model_config']['tie_projs']
        tie_weight = checkpoint['model_config']['tie_weight']
        div_val = checkpoint['model_config']['div_val']
        d_model = checkpoint['model_config']['d_model']
        d_embed = checkpoint['model_config']['d_embed']

        if div_val != 1 or d_model != d_embed:
            for i in range(len(model.word_emb.emb_projs)):
                model.word_emb.emb_projs[i] = state[f'word_emb.emb_projs.{i}'].to(dtype)

        for i in range(len(model.crit.out_projs)):
            if div_val == 1:
                src = 0
            else:
                src = i
            if model.crit.out_projs[i] is not None:
                if tie_projs[i]:
                    model.crit.out_projs[i] = state[f'word_emb.emb_projs.{src}'].to(dtype)
                else:
                    model.crit.out_projs[i] = state[f'crit.out_projs.{i}'].to(dtype)

        for i in range(len(model.crit.out_layers_biases)):
            model.crit.out_layers_biases[i] = state[f'crit.out_layers_biases.{i}'].to(dtype)

        if tie_weight:
            for i in range(len(model.crit.out_layers_weights)):
                model.crit.out_layers_weights[i] = state[f'word_emb.emb_layers.{i}.weight'].to(dtype)
        else:
            for i in range(len(model.crit.out_layers_weights)):
                model.crit.out_layers_weights[i] = state[f'crit.out_layers_weights.{i}'].to(dtype)

        model = torch.jit.script(model)

    if args.type != 'pytorch':
        compile_model(model, device, args)

    if args.type == 'torchscript' and args.save_torchscript:
        torch.jit.save(model, args.save_torchscript)

    logging.info(f'Evaluating with: math {math_str} type {args.type} '
                 f'bsz {args.batch_size} tgt_len {args.tgt_len} '
                 f'ext_len {args.ext_len} mem_len {args.mem_len} '
                 f'clamp_len {args.clamp_len}')

    meters = {}
    warmup = args.mem_len // args.tgt_len + 2
    meters['eval_throughput'] = AverageMeter(warmup=warmup, keep=args.save_data)
    meters['eval_latency'] = AverageMeter(warmup=warmup, keep=args.save_data)

    with torch.autograd.profiler.emit_nvtx(enabled=args.profile):
        loss = evaluate(iter, model, meters, args.log_interval, args.max_size,
                        args.repeat)
    perplexity = math.exp(loss)
    log_str = format_log(loss, args.split, args)

    summary = {
        'eval_loss': loss,
        'eval_ppl': perplexity,
        }

    logging.info('=' * 100)
    logging.info(log_str)
    logging.info('=' * 100)

    if args.save_data:
        latency_data = np.array(meters['eval_latency'].vals)
        throughput_data = np.array(meters['eval_throughput'].vals)
        precision = 'fp16' if args.fp16 else 'fp32'
        data_fname = f'eval_data_{args.batch_size}_{precision}_{args.type}'
        data_path = os.path.join(args.work_dir, data_fname)
        data = {
            'args': args,
            'throughput': throughput_data,
            'latency': latency_data,
            }
        with open(data_path, 'wb') as f:
            pickle.dump(data, f)
        logging.info(f'Throughput Avg: {throughput_data.mean():.2f} tok/s')
        logging.info(f'Latency Avg: {1000.0 * latency_data.mean():.2f} ms')
        for p in args.percentiles:
            logging.info(f'Latency {p}%: {1000.0 * np.percentile(latency_data, p):.2f} ms')

        logging.info('=' * 100)

        summary.update({
            'eval_throughput': throughput_data.mean(),
            'eval_avg_latency': 1000 * latency_data.mean(),
            })
        for p in args.percentiles:
            summary[f'eval_{p}%_latency'] = 1000 * np.percentile(latency_data, p)

    dllogger.log(step=tuple(), data=summary)

    passed = benchmark(target_perplexity=args.target_perplexity,
                       test_perplexity=perplexity,
                       target_throughput=args.target_throughput,
                       test_throughput=meters['eval_throughput'].avg,
                       )
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    # Disable profiling executor
    try:
        torch._C._jit_set_profiling_executor(False)
        torch._C._jit_set_profiling_mode(False)
    except AttributeError:
        pass

    main()
