# region imports
from AlgorithmImports import *
import pandas as pd
import itertools
from statsmodels.tsa.stattools import coint
from Selection.FundamentalUniverseSelectionModel import FundamentalUniverseSelectionModel
from collections import defaultdict
from datetime import timedelta
# endregion

class MyUniverseSelectionModel(FundamentalUniverseSelectionModel):
    """
    贝异斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    def __init__(self, algorithm):
        super().__init__(True)
        self.algorithm = algorithm
        self.numOfCandidates = 50
        self.cointegrated_pairs = {}
        self.lookback_period = 252
        self.pvalue_threshold = 0.01
        self.max_holding_days = timedelta(days=15)

        self.min_price = 10
        self.min_market_cap = 1e9
        self.min_volume = 10000000
        self.min_ipo_days = 365
        self.max_pairs = 10
        self.max_symbol_repeats = 1

        self.last_coarse_symbols = []
        self.last_fine_symbols = []
        algorithm.Debug("[UniverseSelection] 初始化完成")


    def _rebalance_flag(self):
        """
        判断是否需要重新平衡
        """
        now = self.algorithm.Time
        if not self.cointegrated_pairs:
            return True

        all_expired = all((now - v['create_time']).days >= v['max_holding_days'].days for v in self.cointegrated_pairs.values())
        return all_expired



    def SelectCoarse(self, algorithm, coarse):
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票
        """
        if not self._rebalance_flag():
            return self.last_coarse_symbols
        
        algorithm.Debug("[UniverseSelection] -- [SelectCoarse] 被调用")

        filtered = [x for x in coarse if x.HasFundamentalData and x.Price > self.min_price and x.MarketCap > self.min_market_cap and x.Volume > self.min_volume]
        IpoFiltered = [x for x in filtered if x.SecurityReference.IPODate is not None and (algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]
        CoarseSelected = [x.Symbol for x in sorted(IpoFiltered, key=lambda x: x.Symbol.Value)]

        self.last_coarse_symbols = CoarseSelected

        return CoarseSelected



    def SelectFine(self, algorithm, fine):
        """
        细选阶段，负责从粗选的股票中筛选出符合条件的股票
        """
        if not self._rebalance_flag():
            algorithm.Debug(f"[UniverseSelection] -- [SelectFine] fine为空, 返回上轮订阅的symbol: {[x.Value for x in self.last_fine_symbols]}")
            return self.last_fine_symbols

        algorithm.Debug("[UniverseSelection] -- [SelectFine] 被调用")

        techCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Technology]
        healthcareCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Healthcare]
        energyCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Energy]
        consumerDefensiveCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.ConsumerDefensive]
        communicationCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.CommunicationServices]
        industrialsCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Industrials]
        utilitiesCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Utilities]

        techSelected = self.NumOfCandidates(techCandidates)
        healthcareSelected = self.NumOfCandidates(healthcareCandidates)
        energySelected = self.NumOfCandidates(energyCandidates)
        consumerDefensiveSelected = self.NumOfCandidates(consumerDefensiveCandidates)
        communicationSelected = self.NumOfCandidates(communicationCandidates)
        industrialsSelected = self.NumOfCandidates(industrialsCandidates)
        utilitiesSelected = self.NumOfCandidates(utilitiesCandidates)

        sectorSelected = techSelected + healthcareSelected + \
                         energySelected + consumerDefensiveSelected + \
                         communicationSelected + industrialsSelected + utilitiesSelected  

        potential_pairs = list(itertools.combinations(sectorSelected, 2))
        cointegrated_pairs_unfiltered = self.CointegrationTestForPairs(potential_pairs)
        cointegrated_pairs = self.FilterCointegratedPairs(cointegrated_pairs_unfiltered)
        self.cointegrated_pairs = cointegrated_pairs

        selected_symbols = set()
        for cointegrated_pair in cointegrated_pairs:
            s1, s2 = cointegrated_pair  
            selected_symbols.add(s1)
            selected_symbols.add(s2)

        self.last_fine_symbols = list(selected_symbols)
        algorithm.Debug(f"[UniverseSelection] -- [SelectFine] 最终筛选出 {len(selected_symbols)} 只股票，形成 {len(cointegrated_pairs)} 个协整对")

        return list(selected_symbols)



    def NumOfCandidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票
        """
        filtered = [x.symbol for x in sorted(sectorCandidates, key=lambda x: x.DollarVolume, reverse=True)]
        return filtered[:self.numOfCandidates] if len(filtered) > self.numOfCandidates else filtered



    def CointegrationTestForSinglePair(self, symbol1, symbol2):
        """
        单个股票对协整检验
        """
        historySymbol1 = self.algorithm.History([symbol1], self.lookback_period, Resolution.Daily)
        historySymbol2 = self.algorithm.History([symbol2], self.lookback_period, Resolution.Daily)

        if historySymbol1.empty or historySymbol2.empty or len(historySymbol1.index.levels[1].unique()) < 2:
            return False, 1.0, None

        try:
            price1 = historySymbol1.loc[symbol1]['close'].fillna(method='ffill')
            price2 = historySymbol2.loc[symbol2]['close'].fillna(method='ffill')
            if len(price1) != len(price2) or price1.isnull().any() or price2.isnull().any():
                return False, 1.0, None
            correlation = price1.corr(price2)
            if abs(correlation) < 0.7:
                return False, 1.0, None
            score, pvalue, critical_values = coint(price1, price2)
            is_cointegrated = pvalue < self.pvalue_threshold
            return is_cointegrated, pvalue, critical_values
        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForSinglePair] 协整检验出错: {str(e)}")
            return False, 1.0, None



    def CointegrationTestForPairs(self, pairs):
        """
        多个股票对协整检验
        """
        cointegrated_pairs = {}
        for symbol1, symbol2 in pairs:
            is_cointegrated, pvalue, critical_values = self.CointegrationTestForSinglePair(symbol1, symbol2)
            if is_cointegrated:
                pair_key = (symbol1, symbol2)
                cointegrated_pairs[pair_key] = {
                    'pvalue': pvalue,
                    'critical_values': critical_values,
                    'create_time': self.algorithm.Time,
                    'max_holding_days': self.max_holding_days
                }
                self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForPairs] 发现协整对: [{symbol1.Value} - {symbol2.Value}], p值: {pvalue:.4f}")
        return cointegrated_pairs



    def FilterCointegratedPairs(self, cointegrated_pairs) -> dict:
        """
        过滤协整对
        """
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = {}
        for keys, values in sorted_pairs:
            s1, s2 = keys
            if symbol_count[s1] < self.max_symbol_repeats and symbol_count[s2] < self.max_symbol_repeats:
                filtered_pairs[keys] = values
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) > self.max_pairs:
                break
        return filtered_pairs





