#!/usr/bin/env python
# coding=utf-8
# Copyright 2020 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Fine-tuning the library models for causal language modeling (GPT, GPT-2, CTRL, ...) on a text file or a dataset.

Here is the full list of checkpoints on the hub that can be fine-tuned by this script:
https://huggingface.co/models?filter=causal-lm
"""
# You can also adapt this script on your own causal language modeling task. Pointers for this are left as comments.

import logging
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from datasets import load_dataset
from datasets.arrow_dataset import Dataset

import transformers
from transformers import (
    CONFIG_MAPPING,
    MODEL_FOR_CAUSAL_LM_MAPPING,
    AutoConfig,
    AutoModelForCausalLM,
    AutoTokenizer,
    HfArgumentParser,
    Trainer,
    TrainingArguments,
    default_data_collator,
    set_seed,
    PretrainedConfig
)
from transformers.tokenization_utils import PreTrainedTokenizer
from transformers.trainer_utils import get_last_checkpoint, is_main_process


logger = logging.getLogger(__name__)


@dataclass
class ModelArguments:
    model_name_or_path: Optional[str] = field(
        default=None,
        metadata={
            "help": "The model checkpoint for weights initialization."
            "Don't set if you want to train a model from scratch."
        },
    )
    tokenizer_name_or_path: Optional[str] = field(
        default=None,
        metadata={
            "help": "Tokenizer name or path"
        },
    )
    cache_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Where do you want to store the pretrained models downloaded from huggingface.co"},
    )
    use_fast_tokenizer: bool = field(
        default=True,
        metadata={"help": "Whether to use one of the fast tokenizer (backed by the tokenizers library) or not."},
    )
    model_revision: str = field(
        default="main",
        metadata={"help": "The specific model version to use (can be a branch name, tag name or commit id)."},
    )
    use_auth_token: bool = field(
        default=False,
        metadata={
            "help": "Will use the token generated when running `transformers-cli login` (necessary to use this script "
            "with private models)."
        },
    )


@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """

    dataset_name: Optional[str] = field(
        default=None, metadata={"help": "The name of the dataset to use (via the datasets library), ex. 'wikitext'"}
    )
    dataset_config_name: Optional[str] = field(
        default=None, metadata={"help": "The configuration name of the dataset to use (via the datasets library), ex. 'wikitext-103-raw-v1'"}
    )
    train_file: Optional[str] = field(default=None, metadata={"help": "The input training data file (a text file)."})
    validation_file: Optional[str] = field(
        default=None,
        metadata={"help": "An optional input evaluation data file to evaluate the perplexity on (a text file)."},
    )
    block_size: Optional[int] = field(
        default=None,
        metadata={
            "help": "Optional input sequence length after tokenization."
            "The training dataset will be truncated in block of this size for training."
            "Default to the model max input length for single sentence inputs (take into account special tokens)."
        },
    )
    overwrite_cache: bool = field(
        default=False, metadata={"help": "Overwrite the cached training and evaluation sets"}
    )
    validation_split_percentage: Optional[int] = field(
        default=5,
        metadata={
            "help": "The percentage of the train set used as validation set in case there's no validation split"
        },
    )
    preprocessing_num_workers: Optional[int] = field(
        default=None,
        metadata={"help": "The number of processes to use for the preprocessing."},
    )
    data_dir: Optional[str] = field(
        default=None, metadata={"help": "Cache directory to store downloaded dataset"}
    )

    def __post_init__(self):
        if self.dataset_name is None and self.train_file is None and self.validation_file is None:
            raise ValueError("Need either a dataset name or a training/validation file.")
        else:
            if self.train_file is not None:
                extension = self.train_file.split(".")[-1]
                assert extension in ["csv", "json", "txt"], "`train_file` should be a csv, a json or a txt file."
            if self.validation_file is not None:
                extension = self.validation_file.split(".")[-1]
                assert extension in ["csv", "json", "txt"], "`validation_file` should be a csv, a json or a txt file."

def get_checkpoint(output_dir:str, overwrite_output_dir:bool)->Optional[str]:
    # Detecting last checkpoint.
    last_checkpoint = None
    if os.path.isdir(output_dir) and not overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(output_dir)
        if last_checkpoint is None and len(os.listdir(output_dir)) > 0:
            raise ValueError(
                f"Output directory ({output_dir}) already exists and is not empty. "
                "Use overwrite_output_dir=True"
            )
        elif last_checkpoint is not None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                 "Use overwrite_output_dir=True"
            )
    return last_checkpoint

def setup_logging(local_rank:int):
    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger.setLevel(logging.INFO if is_main_process(local_rank) else logging.WARN)

    # Set the verbosity to info of the Transformers logger (on main process only):
    if is_main_process(local_rank):
        transformers.utils.logging.set_verbosity_info()
        transformers.utils.logging.enable_default_handler()
        transformers.utils.logging.enable_explicit_format()

def dataset_from_files(train_file:Optional[str], validation_file:Optional[str])->Dataset:
    data_files = {}
    if train_file is not None:
        data_files["train"] = train_file
    if validation_file is not None:
        data_files["validation"] = validation_file
    extension = (
        train_file.split(".")[-1]
        if train_file is not None
        else validation_file.split(".")[-1]
    )
    if extension == "txt":
        extension = "text"
    datasets = load_dataset(extension, data_files=data_files)

    # See more about loading any type of standard or custom dataset (from files, python dict, pandas DataFrame, etc) at
    # https://huggingface.co/docs/datasets/loading_datasets.html.

    return datasets

def dataset_from_name(dataset_name:str, dataset_config_name:Optional[str],
                      data_dir:Optional[str], validation_split_percentage:Optional[int])->Dataset:
    # Get the datasets: you can either provide your own CSV/JSON/TXT training and evaluation files (see below)
    # or just provide the name of one of the public datasets available on the hub at https://huggingface.co/datasets/
    # (the dataset will be downloaded automatically from the datasets Hub).
    #
    # For CSV/JSON files, this script will use the column called 'text' or the first column if no column called
    # 'text' is found. You can easily tweak this behavior (see below).
    #
    # In distributed training, the load_dataset function guarantee that only one local process can concurrently
    # download the dataset.

    # Downloading and loading a dataset from the hub.
    datasets = load_dataset(dataset_name, dataset_config_name,
                            data_dir=data_dir)
    if "validation" not in datasets.keys():
        datasets["validation"] = load_dataset(
            dataset_name,
            dataset_config_name,
            split=f"train[:{validation_split_percentage}%]",
            data_dir=data_dir
        )
        datasets["train"] = load_dataset(
            dataset_name,
            dataset_config_name,
            split=f"train[{validation_split_percentage}%:]",
            data_dir=data_dir
        )
    return datasets

def model_from_pretrained(model_name_or_path:str, revision:str,
                          cache_dir:Optional[str], use_auth_token:Optional[bool])->PreTrainedModel:
    # Load pretrained model and tokenizer
    #
    # Distributed training:
    # The .from_pretrained methods guarantee that only one local process can concurrently
    # download model & vocab.

    config = AutoConfig.from_pretrained(model_name_or_path,
                cache_dir=cache_dir, revision=revision, use_auth_token=use_auth_token)
    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        from_tf=bool(".ckpt" in model_name_or_path),
        config=config,
        cache_dir=cache_dir,
        revision=revision,
        use_auth_token=use_auth_token,
    )
    return model

def model_from_config(model_config:PretrainedConfig)->PreTrainedModel:
    return AutoModelForCausalLM.from_config(model_config)

def tokenizer_from_pretrained(model_name_or_path:str, revision:str, use_fast:bool,
                          cache_dir:Optional[str], use_auth_token:Optional[bool])->PreTrainedTokenizerFast:
    return AutoTokenizer.from_pretrained(model_name_or_path,
        cache_dir=cache_dir,
        revision=revision,
        use_auth_token=use_auth_token, use_fast=use_fast)

def create_lm_datasets(do_train:bool, datasets:Dataset, tokenizer:PreTrainedTokenizer,
                       preprocessing_num_workers:Optional[int], overwrite_cache:bool,
                       block_size:Optional[int])->Dataset:
    # Preprocessing the datasets.
    # First we tokenize all the texts.
    if do_train:
        column_names = datasets["train"].column_names
    else:
        column_names = datasets["validation"].column_names
    text_column_name = "text" if "text" in column_names else column_names[0]

    # bind function to column name
    def tokenize_function(examples):
        return tokenizer(examples[text_column_name])

    tokenized_datasets = datasets.map(
        tokenize_function,
        batched=True,
        num_proc=preprocessing_num_workers,
        remove_columns=column_names,
        load_from_cache_file=not overwrite_cache,
    )

    if block_size is None:
        block_size = tokenizer.model_max_length
        if block_size > 1024:
            logger.warn(
                f"The tokenizer picked seems to have a very large `model_max_length` ({tokenizer.model_max_length}). "
                "Picking 1024 instead. You can change that default value by passing --block_size xxx."
            )
        block_size = 1024
    else:
        if block_size > tokenizer.model_max_length:
            logger.warn(
                f"The block_size passed ({block_size}) is larger than the maximum length for the model"
                f"({tokenizer.model_max_length}). Using block_size={tokenizer.model_max_length}."
            )
        block_size = min(block_size, tokenizer.model_max_length)

    # Main data processing function that will concatenate all texts from our dataset and generate chunks of block_size.
    def group_texts(examples):
        # Concatenate all texts.
        concatenated_examples = {k: sum(examples[k], []) for k in examples.keys()}
        total_length = len(concatenated_examples[list(examples.keys())[0]])
        # We drop the small remainder, we could add padding if the model supported it instead of this drop, you can
        # customize this part to your needs.
        total_length = (total_length // block_size) * block_size
        # Split by chunks of max_len.
        result = {
            k: [t[i : i + block_size] for i in range(0, total_length, block_size)]
            for k, t in concatenated_examples.items()
        }
        result["labels"] = result["input_ids"].copy()
        return result

    # Note that with `batched=True`, this map processes 1,000 texts together, so group_texts throws away a remainder
    # for each of those groups of 1,000 texts. You can adjust that batch_size here but a higher value might be slower
    # to preprocess.
    #
    # To speed up this part, we use multiprocessing. See the documentation of the map method for more information:
    # https://huggingface.co/docs/datasets/package_reference/main_classes.html#datasets.Dataset.map
    lm_datasets = tokenized_datasets.map(
        group_texts,
        batched=True,
        num_proc=preprocessing_num_workers,
        load_from_cache_file=not overwrite_cache,
    )

    return lm_datasets


def train_main(training_args:TrainingArguments, data_args:DataTrainingArguments,
               model_args:ModelArguments, model_config:Optional[PretrainedConfig]=None):
    # Detecting last checkpoint.
    last_checkpoint = get_checkpoint(training_args.output_dir, training_args.overwrite_output_dir) if training_args.do_train else None

    if data_args.dataset_name is not None:
        datasets = dataset_from_name(data_args.dataset_name,
                                     data_args.dataset_config_name, data_args.data_dir,
                                     data_args.validation_split_percentage)
    elif data_args.train_file is not None:
        datasets = dataset_from_files(data_args.train_file, data_args.validation_file)
    else:
        raise ValueError('Either dataset_name or train_file must be provided')

    tokenizer_name_or_path = model_args.tokenizer_name_or_path or model_args.model_name_or_path
    assert tokenizer_name_or_path
    tokenizer = tokenizer_from_pretrained(tokenizer_name_or_path,
        model_args.model_revision, model_args.use_fast_tokenizer,
        model_args.cache_dir,
        True if model_args.use_auth_token else None)

    if model_args.model_name_or_path:
        model = model_from_pretrained(model_args.model_name_or_path,
                              model_args.model_revision,
                              model_args.cache_dir,
                              True if model_args.use_auth_token else None)
    elif model_config:
        model = model_from_config(model_config)
    else:
        raise ValueError('Either config_name or model_name_or_path or model_config must be provided')
    # if vocab size is not same as input token embedding size then resize input embedding
    model.resize_token_embeddings(len(tokenizer))

    lm_datasets = create_lm_datasets(training_args.do_train, datasets, tokenizer,
                                     data_args.preprocessing_num_workers,
                                     data_args.overwrite_cache, data_args.block_size)

    # Initialize our Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=lm_datasets["train"] if training_args.do_train else None,
        eval_dataset=lm_datasets["validation"] if training_args.do_eval else None,
        tokenizer=tokenizer,
        # Data collator will default to DataCollatorWithPadding, so we change it.
        data_collator=default_data_collator,
    )

    # Training
    if training_args.do_train:
        if last_checkpoint is not None:
            checkpoint = last_checkpoint
        elif model_args.model_name_or_path is not None and os.path.isdir(model_args.model_name_or_path):
            checkpoint = model_args.model_name_or_path
        else:
            checkpoint = None
        train_result = trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model()  # Saves the tokenizer too for easy upload

        metrics = train_result.metrics

        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()

    # Evaluation
    results = {}
    if training_args.do_eval:
        logger.info("*** Evaluate ***")

        eval_dataset = lm_datasets['test'] if 'test' in lm_datasets else None # if none then use val set
        eval_output = trainer.evaluate(eval_dataset=eval_dataset)

        perplexity = math.exp(eval_output["eval_loss"])
        results["perplexity"] = perplexity

        trainer.log_metrics("eval", results)
        trainer.save_metrics("eval", results)

    return results


def main():
    # See all possible arguments in src/transformers/training_args.py
    # or by passing the --help flag to this script.
    # We now keep distinct sets of args, for a cleaner separation of concerns.

    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments))
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        model_args, data_args, training_args = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    else:
        model_args, data_args, training_args = parser.parse_args_into_dataclasses()

    setup_logging(training_args.local_rank)
    logger.info("Training/evaluation parameters %s", training_args)

    # Log on each process the small summary:
    logger.warning(
        f"Process rank: {training_args.local_rank}, device: {training_args.device}, n_gpu: {training_args.n_gpu}"
        + f"distributed training: {bool(training_args.local_rank != -1)}, 16-bits training: {training_args.fp16}"
    )

    # Set seed before initializing model.
    set_seed(training_args.seed)

    train_main(training_args, data_args, model_args, model_config)

def _mp_fn(index):
    # For xla_spawn (TPUs)
    main()


if __name__ == "__main__":
    main()