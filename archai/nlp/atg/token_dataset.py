from typing import List, Optional
import gzip
import itertools

import numpy as np

import torch
from torch.utils.data import Dataset

from transformers import PreTrainedTokenizerFast

from archai.common import utils

class TokenDataset(Dataset):
    def __init__(self, train_file:str, tokenizer: PreTrainedTokenizerFast,
                 block_size: int, text_delim: str = "\n") -> None:

        self.file_path = train_file

        self.tokens = encode_tokens_from_file(
            file_path=self.file_path, eos_token="", tokenizer=tokenizer,
            newline=text_delim)

        assert self.tokens.shape[0] >= block_size, f"There are fewer than {block_size} encoded tokens."
        self.num_subsets = self.tokens.shape[0] - block_size
        self.block_size = block_size


    def save(self, cache_destination: str = "dataset_cache.tar.gz", compress: bool = True) -> None:
        assert self.tokens.shape[0] > 0, "No data loaded to save."

        if compress:
            open_func = gzip.open
            compress_str = "and compressing "
        else:
            open_func = open
            cache_destination = (
                "dataset_cache.npy"
                if cache_destination == "dataset_cache.tar.gz"
                else cache_destination
            )
            compress_str = ""

        with open_func(cache_destination, "wb") as f:
            np.save(f, self.tokens)

    def __len__(self) -> int:
        return self.num_subsets

    def __getitem__(self, item: int) -> torch.Tensor:
        return torch.as_tensor(
            self.tokens[item : (item + self.block_size)].astype(np.int64, copy=False),
            dtype=torch.long,
        )

    def __str__(self) -> str:
        return self.file_path if self.file_path is not None else "loaded dataset"

    def __repr__(self) -> str:
        return f"TokenDataset containing {self.num_subsets:,} subsets loaded"

def encode_tokens_from_file(file_path: str, eos_token: str,
    tokenizer: PreTrainedTokenizerFast,newline: str,
    batch_size: int = 1024) -> List[int]:
    """
    Retrieves texts from a newline-delimited file/CSV and returns texts.
    """

    a_dtype = get_dtype(tokenizer.vocab_size)

    num_texts = get_lines_in_file(file_path, newline)

    tokens = np.full((num_texts, 1), -1, dtype=a_dtype)
    num_batches = 0

    with open(file_path, "r", encoding="utf-8", newline=newline) as f_load:
        f_read = f_load

        # https://stackoverflow.com/a/6335876/9314418
        while True:
            batch = [text + eos_token
                    for text in list(itertools.islice(f_read, batch_size))]

            if not batch:
                break

            encoded_texts = tokenizer.batch_encode_plus(
                batch, add_special_tokens=False, return_token_type_ids=False,
                return_attention_mask=False)["input_ids"]

            for i, encoded_text in enumerate(encoded_texts):
                if len(encoded_text) > tokens.shape[1]:
                    cols_to_add = len(encoded_text) - tokens.shape[1]
                    tokens = np.concatenate(
                        (
                            tokens,
                            np.full(
                                (num_texts, cols_to_add),
                                -1,
                                dtype=a_dtype,
                            ),
                        ),
                        axis=1,
                    )
                tokens[
                    (num_batches * batch_size) + i, : len(encoded_text)
                ] = encoded_text

            num_batches += 1

    tokens = tokens.flatten()
    return tokens[tokens < np.array(-1, dtype=a_dtype)]

def get_lines_in_file(file_path: str, newline: str = None) -> int:
    """
    Returns the number of lines in a file to build progress bar.
    c.f. https://stackoverflow.com/a/16108605/9314418
    """

    with open(file_path, "r", encoding="utf-8", newline=newline) as f:
        return sum(1 for row in f)


def get_dtype(vocab_size: int):
    """
    Finds the appropriate numpy dtype depending on vocab size.

    The highest value for the dtype serves as a placeholder.
    """
    if vocab_size < 2 ** 8 - 1:
        return np.uint8
    elif vocab_size < 2 ** 16 - 1:
        return np.uint16
    elif vocab_size < 2 ** 32 - 1:
        return np.uint32

    return np.uint64