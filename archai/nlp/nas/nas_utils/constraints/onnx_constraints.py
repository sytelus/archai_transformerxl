# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ONNX-based constraints.
"""

import timeit
from typing import Any, Dict, Optional

import numpy as np

from archai.nlp.compression.onnx.onnx_utils.export import export_onnx_from_torch
from archai.nlp.compression.onnx.onnx_utils.onnx_loader import (load_from_config_for_export,
                                                                load_from_onnx)
from archai.nlp.compression.onnx.onnx_utils.optimization import optimize_onnx
from archai.nlp.compression.quantization.ptq import dynamic_quantization_onnx
from archai.nlp.models.model_loader import load_model_formula


def _prepare_onnx_model(model_type: str,
                        model_config: Dict[str, Any],
                        use_quantization: bool) -> str:
    """Prepares an ONNX-based model ready for export.

    Args:
        model_type: Type of model.
        model_config: Model's configuration.
        use_quantization: Whether latency should be calculated with quantizated model or not.

    Returns:
        (str): Path to the ONNX-based model.

    """

    export_model, model_config = load_from_config_for_export(model_type, model_config)

    onnx_model_path = 'temp.onnx'
    export_onnx_from_torch(export_model,
                           model_config,
                           model_type,
                           onnx_model_path,
                           share_weights=True,
                           opset_version=12)
    
    onnx_model_path = optimize_onnx(model_type,
                                    onnx_model_path,
                                    num_heads=model_config['n_head'],
                                    opt_level=0)

    if use_quantization:
        onnx_model_path = dynamic_quantization_onnx(onnx_model_path)

    return str(onnx_model_path)


def measure_onnx_inference_latency(model_type: str,
                                   model_config: Dict[str, Any],
                                   use_quantization: Optional[bool] = False,
                                   use_median: Optional[bool] = False,
                                   batch_size: Optional[int] = 1,
                                   seq_len: Optional[int] = 192,
                                   n_trials: Optional[int] = 10) -> float:
    """Measures a ONNX-based model's inference latency.

    Args:
        model_type: Type of model.
        model_config: Model's configuration.
        use_quantization: Whether latency should be calculated with quantizated model or not.
        use_median: Whether should use median instead of mean for latency measurement.
        batch_size: Batch size to measure the latency.
        seq_len: Sequence length to measure the latency.
        n_trials: Number of times to repeat the measurement.

    Returns:
        (float): Mean or median latency in seconds.

    """

    onnx_model_path = _prepare_onnx_model(model_type, model_config, use_quantization)
    onnx_model_session = load_from_onnx(onnx_model_path)

    n_past_values = 2
    if model_type == 'mem_transformer':
        if model_config['attn_type'] == 0:
            n_past_values = 3

    inputs = {'input_ids': np.random.randint(0, model_config['n_token'], (batch_size, seq_len))}
    for i in range(model_config['n_layer']):
        key = f'past_{i}'
        inputs[key] =  np.zeros((n_past_values, batch_size, model_config['n_head'], 0, model_config['d_head']), dtype=np.float32)

    timer = timeit.Timer(stmt='onnx_model_session(None, inputs)',
                         globals={
                            'inputs': inputs,
                            'onnx_model_session': onnx_model_session.run
                        })

    # Performs a quick warmup prior to the calculation
    _ = timer.timeit(number=max(int(n_trials // 100), 2))

    # Calculates proper set of times (instead of sum)
    runner = timer.repeat(repeat=n_trials, number=n_trials)
    runner = [r / n_trials for r in runner]
    
    return np.median(runner) if use_median else np.mean(runner)


def measure_onnx_parameters(model_type: str,
                            model_config: Dict[str, Any],
                            key: Optional[str] = 'non_embedding') -> int:
    """Measures a model's number of parameters according to input options.

    Args:
        model_type: Type of model.
        model_config: Model's configuration.
        key: Key that should be used in measurement.

    Returns:
        (int): Number of parameters.

    """

    return load_model_formula(model_type)(model_config)[key]
