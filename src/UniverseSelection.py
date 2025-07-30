# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
from typing import List, Dict, Tuple
from collections import defaultdict
# endregion

class MyUniverseSelectionModel(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    def __init__(self, algorithm, config, sector_code_to_name, sector_name_to_code):
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name
        self.sector_name_to_code = sector_name_to_code
        
        # 状态管理
        self.selection_on = False
        self.last_fine_selected_symbols = []
        self.fine_selection_count = 0
        
        algorithm.Debug("[UniverseSelection] 初始化完成")
        super().__init__(self._select_coarse, self._select_fine)

    def TriggerSelection(self):
        """外部调用的触发接口"""
        self.selection_on = True

    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """粗选阶段：基础筛选"""
        if not self.selection_on:
            return self.last_fine_selected_symbols
        
        # 基础条件筛选
        return [
            x.Symbol for x in coarse 
            if all([
                x.HasFundamentalData,
                x.Price > self.config['min_price'],
                x.DollarVolume > self.config['min_volume'],
                x.SecurityReference.IPODate is not None,
                (self.algorithm.Time - x.SecurityReference.IPODate).days > self.config['min_days_since_ipo']
            ])
        ]

    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """精选阶段：行业和财务筛选"""
        if not self.selection_on:
            return self.last_fine_selected_symbols
            
        # 重置选股标志
        self.selection_on = False 
        self.fine_selection_count += 1
        self.algorithm.Debug(f"===== 第【{self.fine_selection_count}】次选股 =====")
        
        # 按行业分组并选取
        sector_candidates = self._group_by_sector(fine)
        
        # 应用财务筛选
        filtered_stocks, filter_stats = self._apply_financial_filters(sector_candidates)
        
        # 更新状态
        self.last_fine_selected_symbols = [x.Symbol for x in filtered_stocks]
        
        # 输出统计日志
        self._log_selection_results(sector_candidates, filtered_stocks, filter_stats)
        
        return self.last_fine_selected_symbols

    def _group_by_sector(self, fine: List[FineFundamental]) -> List[FineFundamental]:
        """按行业分组并选取每个行业的前N只股票"""
        all_selected = []
        
        for sector_name, sector_code in self.sector_name_to_code.items():
            # 筛选该行业的股票
            sector_stocks = [x for x in fine if x.AssetClassification.MorningstarSectorCode == sector_code]
            
            # 选取市值最大的前N只
            selected = self._select_top_by_market_cap(sector_stocks, self.config['max_stocks_per_sector'])
            all_selected.extend(selected)
            
        return all_selected

    def _select_top_by_market_cap(self, stocks: List[FineFundamental], n: int) -> List[FineFundamental]:
        """选取市值最大的前N只股票"""
        valid_stocks = [x for x in stocks if x.MarketCap is not None and x.MarketCap > 0]
        return sorted(valid_stocks, key=lambda x: x.MarketCap, reverse=True)[:n]

    def _apply_financial_filters(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """应用财务筛选条件"""
        filtered_stocks = []
        
        # 初始化统计计数器
        stats = defaultdict(int, total=len(stocks), passed=0)
        
        for stock in stocks:
            passed, fail_reasons = self._check_financial_criteria(stock)
            
            if passed:
                filtered_stocks.append(stock)
                stats['passed'] += 1
            else:
                # 更新失败统计
                for reason in fail_reasons:
                    stats[reason] += 1
        
        return filtered_stocks, stats

    def _check_financial_criteria(self, stock: FineFundamental) -> Tuple[bool, List[str]]:
        """检查单只股票的财务指标"""
        fail_reasons = []
        
        try:
            # 检查数据完整性
            if stock.ValuationRatios is None or stock.OperationRatios is None:
                return False, ['data_missing']
            
            # 定义筛选条件
            criteria = {
                'pe_failed': (stock.ValuationRatios.PERatio, lambda x: x is not None and x < self.config['max_pe']),
                'roe_failed': (stock.OperationRatios.ROE.Value, lambda x: x is not None and x > self.config['min_roe']),
                'debt_failed': (stock.OperationRatios.DebtToAssets.Value, lambda x: x is not None and x < self.config['max_debt_ratio']),
                'leverage_failed': (stock.OperationRatios.FinancialLeverage.Value, lambda x: x is not None and x < self.config['max_leverage_ratio'])
            }
            
            # 检查每个条件
            for reason, (value, condition) in criteria.items():
                if not condition(value):
                    fail_reasons.append(reason)
            
            return len(fail_reasons) == 0, fail_reasons
            
        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] 财务数据异常 {stock.Symbol}: {str(e)[:50]}")
            return False, ['data_missing']

    def _log_selection_results(self, candidates: List[FineFundamental], filtered: List[FineFundamental], stats: Dict[str, int]):
        """输出选股结果日志"""
        # 基础统计
        self.algorithm.Debug(f"[UniverseSelection] 财务筛选: 候选{stats['total']}只 → 通过{stats['passed']}只 (过滤{stats['total'] - stats['passed']}只)")
        
        # 财务筛选明细
        failed_details = []
        reason_map = {
            'pe_failed': 'PE',
            'roe_failed': 'ROE',
            'debt_failed': '债务',
            'leverage_failed': '杠杆',
            'data_missing': '缺失'
        }
        
        for reason, label in reason_map.items():
            if stats[reason] > 0:
                failed_details.append(f"{label}({stats[reason]})")
        
        if failed_details:
            self.algorithm.Debug(f"[UniverseSelection] 财务过滤明细: {' '.join(failed_details)}")
        
        # 行业分布统计
        sector_distribution = defaultdict(int)
        for stock in filtered:
            sector_name = self.sector_code_to_name.get(stock.AssetClassification.MorningstarSectorCode)
            if sector_name:
                sector_distribution[sector_name] += 1
        
        if sector_distribution:
            sector_stats = " ".join([f"{name}({count})" for name, count in sorted(sector_distribution.items())])
            self.algorithm.Debug(f"[UniverseSelection] 行业分布: {sector_stats}")
        
        # 最终结果
        final_symbols = [x.Symbol for x in filtered]
        sample = [x.Value for x in final_symbols[:5]]
        self.algorithm.Debug(f"[UniverseSelection] 选股完成: 共{len(final_symbols)}只股票, 前5样本: {sample}")