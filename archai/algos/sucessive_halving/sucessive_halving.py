import random
from abc import ABCMeta, abstractmethod
from overrides.overrides import overrides
from typing import Tuple, List, Union, Dict, Optional
from tqdm import tqdm
from pathlib import Path

import numpy as np

from archai.common.common import logger
from archai.nas.arch_meta import ArchWithMetaData
from archai.search_spaces.discrete.base import DiscreteSearchSpaceBase
from archai.metrics.base import BaseMetric, BaseAsyncMetric
from archai.metrics import evaluate_models, get_non_dominated_sorting, SearchResults
from archai.nas.searcher import Searcher
from archai.common.config import Config
from archai.datasets.data import DatasetProvider

class SucessiveHalvingAlgo(Searcher):
    def __init__(self, search_space: DiscreteSearchSpaceBase, 
                 objectives: Dict[str, Union[BaseMetric, BaseAsyncMetric]], 
                 dataset_provider: DatasetProvider,
                 output_dir: str, num_iters: int = 10,
                 init_num_models: int = 10,
                 init_budget: float = 1.0,
                 budget_multiplier: float = 2.0,
                 seed: int = 1):
        
        assert isinstance(search_space, DiscreteSearchSpaceBase)

        # Search parameters
        self.search_space = search_space
        self.objectives = objectives
        self.dataset_provider = dataset_provider
        self.output_dir = Path(output_dir)
        self.num_iters = num_iters
        self.init_num_models = init_num_models
        self.init_budget = init_budget
        self.budget_multiplier = budget_multiplier

        # Utils
        self.iter_num = 0
        self.num_sampled_models = 0
        self.seed = seed
        self.search_state = SearchResults(search_space, objectives)
        self.rng = random.Random(seed)

        self.output_dir.mkdir(exist_ok=True, parents=True)

    def sample_init_models(self, sample_size: int) -> List[ArchWithMetaData]:
        architectures = [
            self.search_space.get([self.seed + i]) for i in range(sample_size)
        ]
        
        self.num_sampled_models += sample_size
        return architectures

    @overrides
    def search(self) -> SearchResults:
        current_budget = self.init_budget
        population = self.sample_init_models(self.init_num_models)
        selected_models = population

        for i in range(self.num_iters):
            if len(selected_models) <= 1:
                logger.info(f'Search ended. Architecture selected: {selected_models[0].metadata["archid"]}')
                self.search_space.save_arch(selected_models[0], self.output_dir / 'final_model')
                
                break

            logger.info(
                f'Starting iteration {i} with {len(selected_models)} architectures '
                f'and budget of {current_budget}.'
            )

            logger.info(f'Evaluating {len(selected_models)} models with budget {current_budget}..')
            results = evaluate_models(selected_models, self.objectives, self.dataset_provider, budgets={
                obj_name: current_budget
                for obj_name in self.objectives
            })

            # Logs results and saves iteration models
            self.search_state.add_iteration_results(
                selected_models, results,
                extra_model_data={
                    'budget': [current_budget] * len(selected_models)
                }
            )

            models_dir = self.output_dir / f'models_iter_{self.iter_num}'
            models_dir.mkdir(exist_ok=True)

            for model in selected_models:
                self.search_space.save_arch(model, str(models_dir / f'{model.metadata["archid"]}'))

            self.search_state.save_search_state(
                str(self.output_dir / f'search_state_{self.iter_num}.csv')
            )

            self.search_state.save_all_2d_pareto_evolution_plots(self.output_dir)

            # Keeps only the best `1/self.budget_multiplier` NDS frontiers
            logger.info(f'Choosing models for the next iteration..')
            nds_frontiers = get_non_dominated_sorting(selected_models, results, self.objectives)
            nds_frontiers = nds_frontiers[:int(len(nds_frontiers) * 1/self.budget_multiplier)]

            selected_models = [model for frontier in nds_frontiers for model in frontier['models']]
            logger.info(f'Kept {len(selected_models)} models for next iteration.')

            # Update parameters for next iteration
            self.iter_num += 1
            current_budget = current_budget * self.budget_multiplier
        
        return self.search_state
