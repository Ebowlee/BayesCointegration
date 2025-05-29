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
        self.min_price = 10
        self.min_volume = 10000000
        self.min_ipo_days = 365
        self.min_market_cap = 1e9
        self.min_roe = 0.1
        self.max_debt_to_equity = 0.5

        # self.max_pairs = 10
        # self.max_symbol_repeats = 1
        # self.cointegrated_pairs = {}
        # self.lookback_period = 252
        # self.pvalue_threshold = 0.01

        algorithm.Debug("[UniverseSelection] 初始化完成")



    def SelectCoarse(self, algorithm, coarse):
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        filtered = [x for x in coarse if x.HasFundamentalData and x.Price > self.min_price and x.DollarVolume > self.min_volume]
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and (algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]
        coarse_selected = [x.Symbol for x in sorted(ipo_filtered, key=lambda x: x.Symbol.Value)]
        return coarse_selected



    def FineSelectionProcess(self, algorithm, fine):
        """
        细选阶段，主要是依据行业和财报信息
        """
        accounting_filtered = []
        sector_candidates = {}
        sector_selected = {}
        all_selected = []

        # 依据财报信息过滤，防止None报错
        for x in fine:
            roe = getattr(x.Fundamentals, "ROE", None)
            debt_to_equity = getattr(x.Fundamentals, "DebtToEquity", None)
            market_cap = getattr(x, "MarketCap", None)
            if (roe is not None and roe.Value is not None and roe.Value > self.min_roe and
                debt_to_equity is not None and debt_to_equity.Value is not None and debt_to_equity.Value < self.max_debt_to_equity and
                market_cap is not None and market_cap.Value is not None and market_cap.Value > self.min_market_cap):
                accounting_filtered.append(x)

        # 定义 MorningstarSectorCode 到名字的映射
        sector_map = {
            "Technology": MorningstarSectorCode.Technology,
            "Healthcare": MorningstarSectorCode.Healthcare,
            "Energy": MorningstarSectorCode.Energy,
            "ConsumerDefensive": MorningstarSectorCode.ConsumerDefensive,
            "CommunicationServices": MorningstarSectorCode.CommunicationServices,
            "Industrials": MorningstarSectorCode.Industrials,
            "Utilities": MorningstarSectorCode.Utilities
        }

        # 用循环筛选每个行业
        for name, code in sector_map.items():
            candidates = [x for x in accounting_filtered if x.AssetClassification.MorningstarSectorCode == code]
            sector_candidates[name] = candidates

        # 用循环选择每个行业的最终股票
        for name, candidates in sector_candidates.items():
            sector_selected[name] = self.NumOfCandidates(candidates)

        # 汇总所有行业选股结果
        for selected in sector_selected.values():
            all_selected.extend(selected)

        return all_selected



    def NumOfCandidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票，按市值降序排列
        """
        filtered = [x.Symbol for x in sorted(sectorCandidates, key=lambda x: x.MarketCap.Value if (hasattr(x, 'MarketCap') and x.MarketCap.Value is not None) else 0, reverse=True)]
        return filtered[:self.numOfCandidates] if len(filtered) > self.numOfCandidates else filtered
 



    # def CointegrationTestForSinglePair(self, symbol1, symbol2):
    #     """
    #     单个股票对协整检验
    #     """
    #     historySymbol1 = self.algorithm.History([symbol1], self.lookback_period, Resolution.Daily)
    #     historySymbol2 = self.algorithm.History([symbol2], self.lookback_period, Resolution.Daily)

    #     if historySymbol1.empty or historySymbol2.empty or len(historySymbol1.index.levels[1].unique()) < 2:
    #         return False, 1.0, None

    #     try:
    #         price1 = historySymbol1.loc[symbol1]['close'].fillna(method='ffill')
    #         price2 = historySymbol2.loc[symbol2]['close'].fillna(method='ffill')
    #         if len(price1) != len(price2) or price1.isnull().any() or price2.isnull().any():
    #             return False, 1.0, None
    #         correlation = price1.corr(price2)
    #         if abs(correlation) < 0.7:
    #             return False, 1.0, None
    #         score, pvalue, critical_values = coint(price1, price2)
    #         is_cointegrated = pvalue < self.pvalue_threshold
    #         return is_cointegrated, pvalue, critical_values
    #     except Exception as e:
    #         self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForSinglePair] 协整检验出错: {str(e)}")
    #         return False, 1.0, None



    # def CointegrationTestForPairs(self, pairs):
    #     """
    #     多个股票对协整检验
    #     """
    #     cointegrated_pairs = {}
    #     for symbol1, symbol2 in pairs:
    #         is_cointegrated, pvalue, critical_values = self.CointegrationTestForSinglePair(symbol1, symbol2)
    #         if is_cointegrated:
    #             pair_key = (symbol1, symbol2)
    #             cointegrated_pairs[pair_key] = {
    #                 'pvalue': pvalue,
    #                 'critical_values': critical_values,
    #                 'create_time': self.algorithm.Time
    #             }
    #             self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForPairs] 发现协整对: [{symbol1.Value} - {symbol2.Value}], p值: {pvalue:.4f}")
    #     return cointegrated_pairs



    # def FilterCointegratedPairs(self, cointegrated_pairs) -> dict:
    #     """
    #     过滤协整对
    #     """
    #     sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
    #     symbol_count = defaultdict(int)
    #     filtered_pairs = {}
    #     for keys, values in sorted_pairs:
    #         s1, s2 = keys
    #         if symbol_count[s1] < self.max_symbol_repeats and symbol_count[s2] < self.max_symbol_repeats:
    #             filtered_pairs[keys] = values
    #             symbol_count[s1] += 1
    #             symbol_count[s2] += 1
    #         if len(filtered_pairs) > self.max_pairs:
    #             break
    #     return filtered_pairs





