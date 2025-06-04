# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
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
        self.max_debt_to_assets = 0.5

        algorithm.Debug("[UniverseSelection] 初始化完成")

        super().__init__(
            self._select_coarse,  # 传递本类定义的粗选方法
            self._select_fine     # 传递本类定义的精选方法
        )



    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        filtered = [x for x in coarse if x.HasFundamentalData and x.Price > self.min_price and x.DollarVolume > self.min_volume]
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and (self.algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]
        coarse_selected = [x.Symbol for x in sorted(ipo_filtered, key=lambda x: x.Symbol.Value)]
        self.algorithm.Debug(f"[SelectCoarse] 粗选阶段完成, 共选出: {len(coarse_selected)}, 部分结果展示: {[x.Value for x in coarse_selected[:10]]}")
        return coarse_selected



    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
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

        self.algorithm.Debug(f"[SelectFine] 精选阶段完成, 共选出: {len(all_sectors_selected_fine)}, 部分结果展示: {[x.Symbol.Value for x in all_sectors_selected_fine[:10]]}")

        fine_after_financial_filters = []
        for x in all_sectors_selected_fine:
            if x.ValuationRatios is not None and x.OperationRatios is not None:
                pe_ratio = x.ValuationRatios.PERatio
                roe_ratio = x.OperationRatios.ROE.Value
                debt_to_assets_ratio = x.OperationRatios.DebtToAssets.Value

                passes_pe = pe_ratio is not None and pe_ratio < self.max_pe
                passes_roe = roe_ratio is not None and roe_ratio > self.min_roe
                passes_debt_to_assets = debt_to_assets_ratio is not None and debt_to_assets_ratio < self.max_debt_to_assets

                if passes_pe and passes_roe and passes_debt_to_assets:
                    fine_after_financial_filters.append(x)

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
        return sorted_candidates[:self.numOfCandidates]
 





