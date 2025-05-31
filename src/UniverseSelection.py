# region imports
from AlgorithmImports import *
from statsmodels.tsa.stattools import coint
from Selection.FineFundamentalUniverseSelectionModel import FineFundamentalUniverseSelectionModel
# endregion

class MyUniverseSelectionModel(FineFundamentalUniverseSelectionModel):
    """
    贝异斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    def __init__(self, algorithm):
        self.algorithm = algorithm
        self.numOfCandidates = 50
        self.min_price = 10
        self.min_volume = 10000000
        self.min_ipo_days = 365
        self.min_market_cap = 1e9
        self.max_pe = 20
        self.min_roe = 0.05
        self.max_debt_to_equity = 0.8

        algorithm.Debug("[UniverseSelection] 初始化完成")

        super().__init__(
            self._select_coarse,  # 传递本类定义的粗选方法
            self._select_fine     # 传递本类定义的精选方法
        )



    def _select_coarse(self, algorithm, coarse):
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        filtered = [x for x in coarse if x.HasFundamentalData and x.Price > self.min_price and x.DollarVolume > self.min_volume]
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and (algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]
        coarse_selected = [x.Symbol for x in sorted(ipo_filtered, key=lambda x: x.Symbol.Value)]
        self.algorithm.Debug(f"[SelectCoarse] 粗选阶段完成, 共选出: {len(coarse_selected)}, 部分结果展示: {[x.Value for x in coarse_selected[:10]]}")
        return coarse_selected



    def _select_fine(self, algorithm, fine):
        """
        细选阶段，主要是依据行业和财报信息
        """
        sector_candidates = {}
        all_sectors_selected_fine = []

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
            candidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == code]
            sector_candidates[name] = self._num_of_candidates(candidates)

        # 用循环选择每个行业的最终股票
        for selected_fine_list in sector_candidates.values():
            all_sectors_selected_fine.extend(selected_fine_list)

        self.algorithm.Debug(f"[SelectFine] 精选阶段完成, 共选出: {len(all_sectors_selected_fine)}, 部分结果展示: {[x.Value for x in all_sectors_selected_fine[:10]]}")

        fine_after_financial_filters = []
        for x in all_sectors_selected_fine:
            if hasattr(x, 'Fundamentals') and x.Fundamentals is not None:
                pe_ratio_obj = x.Fundamentals.PERatio
                roe_obj = x.Fundamentals.ROE
                debt_to_equity_obj = getattr(x.Fundamentals, 'DebtToEquity', None)

                passes_pe = pe_ratio_obj is not None and pe_ratio_obj < self.max_pe
                passes_roe = roe_obj is not None and roe_obj > self.min_roe
                passes_debt_to_equity = debt_to_equity_obj is not None and debt_to_equity_obj < self.max_debt_to_equity

                if passes_pe and passes_roe and passes_debt_to_equity:
                    fine_after_financial_filters.append(x)
            else:
                self.algorithm.Log(f"[SelectFine] 股票 {x.Symbol.Value} 缺少 Fundamentals 数据.")

        final_selected_symbols = [x.Symbol for x in fine_after_financial_filters]
        
        self.algorithm.Debug(f"[SelectFine] 精选阶段完成, 共选出: {len(final_selected_symbols)}, 部分结果展示: {[x.Value for x in final_selected_symbols[:10]]}")
        return final_selected_symbols



    def _num_of_candidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票，按市值降序排列
        """
       # 返回市值最高的前 self.numOfCandidates 个股票
        filtered = [x for x in sectorCandidates if hasattr(x, 'MarketCap') and x.MarketCap is not None and x.MarketCap > 0]
        sorted_candidates = sorted(filtered, key=lambda x: x.MarketCap, reverse=True)
        return sorted_candidates[:self.numOfCandidates] if len(sorted_candidates) > self.numOfCandidates else sorted_candidates
 



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





