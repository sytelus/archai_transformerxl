# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import argparse

from transformers import (
    DataCollatorForLanguageModeling,
    GPT2Config,
    GPT2LMHeadModel,
    TrainingArguments,
)

from archai.nlp.datasets.hf.loaders import encode_dataset, load_dataset
from archai.nlp.datasets.hf.tokenizer_utils.pre_trained_tokenizer import (
    ArchaiPreTrainedTokenizerFast,
)
from archai.nlp.trainers.hf.trainer import HfTrainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trains a GPT-2 using the Huggingface trainer.")

    parser.add_argument(
        "-dn",
        "--dataset_name",
        type=str,
        default="wikitext",
        help="Name of the dataset to use (via the datasets library).",
    )

    parser.add_argument(
        "-dcn",
        "--dataset_config_name",
        type=str,
        default="wikitext-103-raw-v1",
        help="Configuration name of the dataset to use (via the datasets library).",
    )

    parser.add_argument("-seq", "--seq_len", type=int, default=192, help="Sequence length.")

    parser.add_argument("-ls", "--logging_steps", type=int, default=10, help="Number of steps between logs.")

    parser.add_argument("-es", "--eval_steps", type=int, default=100, help="Number of steps between evaluations.")

    parser.add_argument("-bsz", "--per_device_train_batch_size", type=int, default=64, help="Batch size per device.")

    parser.add_argument("-lr", "--learning_rate", type=float, default=0.01, help="Learning rate.")

    parser.add_argument("-wd", "--weight_decay", type=float, default=0.0, help="Weight decay.")

    parser.add_argument("-n", "--max_steps", type=int, default=250, help="Maximum number of steps.")

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    args = parse_args()

    tokenizer = ArchaiPreTrainedTokenizerFast.from_pretrained("gpt2", model_max_length=args.seq_len)
    tokenizer.add_special_tokens({"pad_token": "[PAD]"})

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    dataset = load_dataset(args.dataset_name, args.dataset_config_name)
    dataset = encode_dataset(dataset, tokenizer)

    config = GPT2Config(
        vocab_size=50257 + 1,
        n_positions=args.seq_len,
        n_embd=512,
        n_layer=16,
        n_head=8,
    )
    model = GPT2LMHeadModel(config=config)

    print(f"Total parameters: {sum(p.numel() for p in model.parameters())}")

    training_args = TrainingArguments(
        "hf-gpt2",
        evaluation_strategy="steps",
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        per_device_train_batch_size=args.per_device_train_batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        max_steps=args.max_steps,
    )
    trainer = HfTrainer(
        model=model,
        args=training_args,
        data_collator=collator,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
    )

    trainer.train()
