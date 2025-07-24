# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
# endregion

class MyUniverseSelectionModel(FineFundamentalUniverseSelectionModel):
    """
    贝异斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    def __init__(self, algorithm, config):
        self.algorithm = algorithm
        self.selection_on = False 
        
        # 强制使用集中配置
        self.max_stocks_per_sector = config['max_stocks_per_sector']
        self.min_price = config['min_price']
        self.min_volume = config['min_volume']
        self.min_days_since_ipo = config['min_days_since_ipo']
        self.max_pe = config['max_pe']
        self.min_roe = config['min_roe']
        self.max_debt_ratio = config['max_debt_ratio']
        self.max_leverage_ratio = config['max_leverage_ratio']

        self.last_fine_selected_symbols = []                            
        self.fine_selection_count = 0
        
        # 定义行业映射（双向）
        self.sector_code_to_name = {
            MorningstarSectorCode.Technology: "Technology",
            MorningstarSectorCode.Healthcare: "Healthcare",
            MorningstarSectorCode.Energy: "Energy",
            MorningstarSectorCode.ConsumerDefensive: "ConsumerDefensive",
            MorningstarSectorCode.ConsumerCyclical: "ConsumerCyclical",
            MorningstarSectorCode.CommunicationServices: "CommunicationServices",
            MorningstarSectorCode.Industrials: "Industrials",
            MorningstarSectorCode.Utilities: "Utilities"
        }
        self.sector_name_to_code = {v: k for k, v in self.sector_code_to_name.items()}

        algorithm.Debug(f"[UniverseSelection] 初始化完成")

        # 初始化父类并传递自定义的粗选和精选方法
        super().__init__(self._select_coarse, self._select_fine)



    def TriggerSelection(self):
        """
        外部调用的触发接口: 用于在 main.py 中通过 Schedule.On 控制
        """
        self.selection_on = True



    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段, 负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        # 只在选股日进行实际筛选
        if not self.selection_on:
            return self.last_fine_selected_symbols
            
        # 基础筛选
        filtered = [x for x in coarse if x.HasFundamentalData and 
                   x.Price > self.min_price and 
                   x.DollarVolume > self.min_volume]
        
        # IPO时间筛选
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and 
                       (self.algorithm.Time - x.SecurityReference.IPODate).days > self.min_days_since_ipo]
        
        coarse_selected = [x.Symbol for x in ipo_filtered]
        
        return coarse_selected



    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        细选阶段, 主要是依据行业和财报信息
        """
        if self.selection_on:
            self.selection_on = False 
            self.fine_selection_count += 1
            self.algorithm.Debug(f"=====================================第【{self.fine_selection_count}】次选股=====================================")

            sector_candidates = {}
            all_sectors_selected_fine = []
            sector_distribution = {}  # 提前初始化行业分布统计

            # 用循环筛选每个行业
            for name, code in self.sector_name_to_code.items():
                candidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == code]
                selected = self._num_of_candidates(candidates)
                if selected:  # 只记录有候选股的行业
                    sector_candidates[name] = selected

            # 把每个行业的股票合并到一起
            for selected_fine_list in sector_candidates.values():
                all_sectors_selected_fine.extend(selected_fine_list)

            fine_after_financial_filters = []
            financial_failed = 0
            pe_failed = 0
            roe_failed = 0 
            debt_failed = 0
            leverage_failed = 0
            data_missing = 0
            
            for x in all_sectors_selected_fine:
                try:
                    if x.ValuationRatios is not None and x.OperationRatios is not None:
                        pe_ratio = x.ValuationRatios.PERatio
                        roe_ratio = x.OperationRatios.ROE.Value
                        debt_to_assets_ratio = x.OperationRatios.DebtToAssets.Value
                        leverage_ratio = x.OperationRatios.FinancialLeverage.Value

                        passes_pe = pe_ratio is not None and pe_ratio < self.max_pe
                        passes_roe = roe_ratio is not None and roe_ratio > self.min_roe
                        passes_debt_to_assets = debt_to_assets_ratio is not None and debt_to_assets_ratio < self.max_debt_ratio
                        passes_leverage_ratio = leverage_ratio is not None and leverage_ratio < self.max_leverage_ratio
                        
                        if passes_pe and passes_roe and passes_debt_to_assets and passes_leverage_ratio:
                            fine_after_financial_filters.append(x)
                            # 同步更新行业分布
                            sector_name = self.sector_code_to_name.get(x.AssetClassification.MorningstarSectorCode)
                            if sector_name:
                                sector_distribution[sector_name] = sector_distribution.get(sector_name, 0) + 1
                        else:
                            financial_failed += 1
                            # 记录具体失败原因
                            if not passes_pe:
                                pe_failed += 1
                            if not passes_roe:
                                roe_failed += 1
                            if not passes_debt_to_assets:
                                debt_failed += 1
                            if not passes_leverage_ratio:
                                leverage_failed += 1
                    else:
                        financial_failed += 1
                        data_missing += 1
                except Exception as e:
                    self.algorithm.Debug(f"[UniverseSelection] 财务数据异常 {x.Symbol}: {str(e)[:50]}")
                    financial_failed += 1
                    data_missing += 1

            final_selected_symbols = [x.Symbol for x in fine_after_financial_filters]
            self.last_fine_selected_symbols = final_selected_symbols
            
            
            # 选股结果统计日志
            total_candidates = len(all_sectors_selected_fine)
            self.algorithm.Debug(f"[UniverseSelection] 财务筛选: 候选{total_candidates}只 → 通过{len(final_selected_symbols)}只 (过滤{financial_failed}只)")
            
            # 财务筛选详细统计
            if financial_failed > 0:
                financial_details = []
                if pe_failed > 0:
                    financial_details.append(f"PE({pe_failed})")
                if roe_failed > 0:
                    financial_details.append(f"ROE({roe_failed})")
                if debt_failed > 0:
                    financial_details.append(f"债务({debt_failed})")
                if leverage_failed > 0:
                    financial_details.append(f"杠杆({leverage_failed})")
                if data_missing > 0:
                    financial_details.append(f"缺失({data_missing})")
                
                if financial_details:
                    self.algorithm.Debug(f"[UniverseSelection] 财务过滤明细: {' '.join(financial_details)}")
            
            # 行业分布统计
            sector_stats = " ".join([f"{name}({count})" for name, count in sorted(sector_distribution.items())])
            self.algorithm.Debug(f"[UniverseSelection] 行业分布: {sector_stats}")
            
            self.algorithm.Debug(f"[UniverseSelection] 选股完成: 共{len(final_selected_symbols)}只股票, 前5样本: {[x.Value for x in final_selected_symbols[:5]]}")
            return final_selected_symbols
        else:
            return self.last_fine_selected_symbols



    def _num_of_candidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票, 按市值降序排列
        """
        # 返回市值最高的前 max_stocks_per_sector 个股票
        filtered = [x for x in sectorCandidates if x.MarketCap is not None and x.MarketCap > 0]
        sorted_candidates = sorted(filtered, key=lambda x: x.MarketCap, reverse=True)
        return sorted_candidates[:self.max_stocks_per_sector]
 





