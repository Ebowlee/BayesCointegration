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
        self.selection_needed = False 
        self.numOfCandidates = 50
        self.min_price = 10
        self.min_volume = 1e7
        self.min_ipo_days = 365
        self.max_pe = 25
        self.min_roe = 0.05
        self.max_debt_to_assets = 0.6
        self.max_leverage_ratio = 5

        self.last_fine_selected_symbols = []                            
        self.fine_selection_count = 0                                   

        algorithm.Debug("[UniverseSelection] 初始化完成")

        # 初始化父类并传递自定义的粗选和精选方法
        super().__init__(self._select_coarse, self._select_fine)



    def TriggerSelection(self):
        """
        外部调用的触发接口：用于在 main.py 中通过 Schedule.On 控制
        """
        self.selection_needed = True
        self.algorithm.Debug("[UniverseSelection] 收到主控调度，准备执行下一轮选股")



    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        filtered = [x for x in coarse if x.HasFundamentalData and x.Price > self.min_price and x.DollarVolume > self.min_volume]
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and (self.algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]
        coarse_selected = [x.Symbol for x in sorted(ipo_filtered, key=lambda x: x.Symbol.Value)]
        return coarse_selected



    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        细选阶段，主要是依据行业和财报信息
        """
        if self.selection_needed:
            self.selection_needed = False 
            self.fine_selection_count += 1
            self.algorithm.Debug(f"=====================================第【{self.fine_selection_count}】次选股=====================================") 

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

            # 把每个行业的股票合并到一起
            for selected_fine_list in sector_candidates.values():
                all_sectors_selected_fine.extend(selected_fine_list)

            fine_after_financial_filters = []
            for x in all_sectors_selected_fine:
                if x.ValuationRatios is not None and x.OperationRatios is not None:
                    pe_ratio = x.ValuationRatios.PERatio
                    roe_ratio = x.OperationRatios.ROE.Value
                    debt_to_assets_ratio = x.OperationRatios.DebtToAssets.Value
                    leverage_ratio = x.OperationRatios.FinancialLeverage.Value

                    passes_pe = pe_ratio is not None and pe_ratio < self.max_pe
                    passes_roe = roe_ratio is not None and roe_ratio > self.min_roe
                    passes_debt_to_assets = debt_to_assets_ratio is not None and debt_to_assets_ratio < self.max_debt_to_assets
                    passes_leverage_ratio = leverage_ratio is not None and leverage_ratio < self.max_leverage_ratio
                    if passes_pe and passes_roe and passes_debt_to_assets and passes_leverage_ratio:
                        fine_after_financial_filters.append(x)

            final_selected_symbols = [x.Symbol for x in fine_after_financial_filters]
            self.last_fine_selected_symbols = final_selected_symbols
            self.algorithm.Debug(f"[UniverseSelection] 选股日, 共选出: {len(final_selected_symbols)}, 部分结果展示: {[x.Value for x in final_selected_symbols[:10]]}")
            return final_selected_symbols
        else:
            return self.last_fine_selected_symbols


    def _num_of_candidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票，按市值降序排列
        """
        # 返回市值最高的前 self.numOfCandidates 个股票
        filtered = [x for x in sectorCandidates if x.MarketCap is not None and x.MarketCap > 0]
        sorted_candidates = sorted(filtered, key=lambda x: x.MarketCap, reverse=True)
        return sorted_candidates[:self.numOfCandidates]
 





