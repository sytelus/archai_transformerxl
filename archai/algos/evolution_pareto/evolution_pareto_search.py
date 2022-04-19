from abc import ABCMeta, abstractmethod
from overrides.overrides import overrides

import abc
import math as ma
from typing import Tuple, List

import torch.nn as nn

from archai.common.common import logger
from archai.nas.arch_meta import ArchWithMetaData
from archai.nas.discrete_search_space import DiscreteSearchSpace
from archai.nas.searcher import Searcher, SearchResult
from archai.common.config import Config


class EvolutionParetoSearch(Searcher):


    @abstractmethod
    def get_search_space(self)->DiscreteSearchSpace:
        pass


    @abstractmethod
    def calc_memory_latency(self, population:List[ArchWithMetaData])->None:
        # computes memory and latency of each model
        # and updates the meta data
        pass

    @abstractmethod
    def calc_task_accuracy(self, population:List[ArchWithMetaData])->None:
        # computes task accuracy of each model
        # and updates the meta data
        pass


    @abstractmethod
    def update_pareto_frontier(self, population:List[ArchWithMetaData])->List[ArchWithMetaData]:
        pass


    @abstractmethod
    def mutate_parents(self, parents:List[ArchWithMetaData])->List[ArchWithMetaData]:
        pass


    @abstractmethod
    def crossover_parents(self, parents:List[ArchWithMetaData])->List[ArchWithMetaData]:
        pass


    def _sample_init_population(self)->List[ArchWithMetaData]:
        init_pop:List[ArchWithMetaData] = []
        while len(init_pop) < self.init_num_models:
            init_pop.append(self.search_space.random_sample())  
        return init_pop


    @overrides
    def search(self, conf_search:Config):
        
        self.init_num_models = conf_search['init_num_models']
        self.num_iters = conf_search['num_iters']
        
        assert self.init_num_models > 0 
        assert self.num_iters > 0

        self.search_space = self.get_search_space()
        assert isinstance(self.search_space, DiscreteSearchSpace)

        # sample the initial population
        unseen_pop:List[ArchWithMetaData] = self._sample_init_population()

        self.all_pop = [unseen_pop]
        for i in range(self.num_iters):
            
            # for the unseen population 
            # calculates the memory and latency
            # and inserts it into the meta data of each member 
            self.calc_memory_latency(unseen_pop)

            # calculate task accuracy proxy
            # could be anything from zero-cost proxy
            # to partial training
            self.calc_task_accuracy(unseen_pop)  

            # update the pareto frontier
            pareto:List[ArchWithMetaData] = self.update_pareto_frontier()

            # select parents for the next iteration from 
            # the current estimate of the frontier while
            # giving more weight to newer parents
            # TODO
            parents = pareto # for now

            # mutate random 'k' subsets of the parents
            # while ensuring the mutations fall within 
            # desired constraint limits
            mutated = self.mutate_parents(parents)

            # crossover random 'k' subsets of the parents
            # while ensuring the mutations fall within 
            # desired constraint limits
            crossovered = self.crossover_parents(parents)

            unseen_pop = crossovered + mutated

            self.all_pop.extend(unseen_pop)


            



            





    