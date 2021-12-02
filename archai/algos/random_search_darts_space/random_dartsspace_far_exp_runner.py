from overrides import overrides
from typing import Optional, Type, Tuple

from archai.nas.exp_runner import ExperimentRunner
from archai.nas.model_desc_builder import ModelDescBuilder
from archai.nas.arch_trainer import TArchTrainer
from archai.common import common
from archai.common import utils
from archai.common.config import Config
from archai.nas.evaluater import Evaluater, EvalResult
from archai.nas.searcher import Searcher, SearchResult
from archai.nas.finalizers import Finalizers
from archai.nas.random_finalizers import RandomFinalizers
from archai.nas.model_desc_builder import ModelDescBuilder
from archai.algos.random_sample_darts_space.random_model_desc_builder import RandomModelDescBuilder
from archai.algos.random_search_darts_space.random_dartsspace_far_searcher import RandomDartsSpaceFarSearcher

class RandomDartsSpaceFarExpRunner(ExperimentRunner):
    ''' Runs random search using FastArchRank on DARTS search space '''

    @overrides
    def model_desc_builder(self)->Optional[ModelDescBuilder]:
        return RandomModelDescBuilder()

    @overrides
    def trainer_class(self)->TArchTrainer:
        return None # no search trainer

    @overrides
    def run_search(self, conf_search:Config)->SearchResult:
        model_desc_builder = self.model_desc_builder()
        trainer_class = self.trainer_class()
        finalizers = self.finalizers()
        search = self.searcher()
        return search.search(conf_search, model_desc_builder, trainer_class, finalizers)

    @overrides
    def run_eval(self, conf_eval:Config)->EvalResult:
        evaler = self.evaluater()
        return evaler.evaluate(conf_eval)

    @overrides
    def searcher(self)->Searcher:
        return RandomDartsSpaceFarSearcher()

    @overrides
    def evaluater(self)->Evaluater:
        return None

    @overrides
    def copy_search_to_eval(self) -> None:
        return None
    
