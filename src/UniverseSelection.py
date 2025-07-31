# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from datetime import timedelta
import numpy as np
# endregion


class MyUniverseSelectionModel(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整选股模型 - 负责选取具有协整关系的资产对
    
    主要功能:
    1. 基础筛选: 价格、交易量、上市时间等基本条件
    2. 行业分组: 按Morningstar行业分类选取每个行业的龙头股
    3. 财务筛选: PE、ROE、债务率、杠杆率等财务指标筛选
    """
    
    # 财务筛选条件配置
    # 定义了需要检查的财务指标及其筛选逻辑
    FINANCIAL_CRITERIA = {
        'pe_failed': {
            'attr_path': ['ValuationRatios', 'PERatio'],  # 市盈率属性路径
            'config_key': 'max_pe',                        # 对应配置项
            'operator': 'lt'                               # 小于阈值才通过
        },
        'roe_failed': {
            'attr_path': ['OperationRatios', 'ROE', 'Value'],  # 净资产收益率路径
            'config_key': 'min_roe',                            # 对应配置项
            'operator': 'gt'                                    # 大于阈值才通过
        },
        'debt_failed': {
            'attr_path': ['OperationRatios', 'DebtToAssets', 'Value'],  # 资产负债率路径
            'config_key': 'max_debt_ratio',                              # 对应配置项
            'operator': 'lt'                                             # 小于阈值才通过
        },
        'leverage_failed': {
            'attr_path': ['OperationRatios', 'FinancialLeverage', 'Value'],  # 财务杠杆路径
            'config_key': 'max_leverage_ratio',                               # 对应配置项
            'operator': 'lt'                                                  # 小于阈值才通过
        }
    }
    

    def __init__(self, algorithm, config, sector_code_to_name, sector_name_to_code):
        """
        初始化选股模型
        
        参数:
            algorithm: QCAlgorithm实例
            config: 配置字典, 包含选股参数
            sector_code_to_name: 行业代码到名称的映射
            sector_name_to_code: 行业名称到代码的映射
        """
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name
        self.sector_name_to_code = sector_name_to_code
        
        # 状态管理
        self.selection_on = False                  # 是否触发选股
        self.last_fine_selected_symbols = []       # 上次选股结果
        self.fine_selection_count = 0              # 选股次数统计
        
        algorithm.Debug("[UniverseSelection] 初始化完成")
        super().__init__(self._select_coarse, self._select_fine)


    def TriggerSelection(self):
        """
        外部调用的触发接口
        由主策略在每月第一个交易日调用
        """
        self.selection_on = True


    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段: 基础筛选
        
        筛选条件:
        1. 有基本面数据
        2. 价格高于最小阈值
        3. 日成交额高于最小阈值  
        4. 上市时间超过最小天数
        
        参数:
            coarse: 粗选数据列表
            
        返回:
            List[Symbol]: 通过粗选的股票代码列表
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols
        
        # 预计算阈值, 避免重复计算
        min_ipo_date = self.algorithm.Time - timedelta(days=self.config['min_days_since_ipo'])
        min_price = self.config['min_price']
        min_volume = self.config['min_volume']
        
        # 高效筛选: 使用预计算值和短路求值
        # 改用成交量(Volume)替代成交金额(DollarVolume)
        return [
            x.Symbol for x in coarse 
            if x.HasFundamentalData
            and x.Price > min_price
            and x.Volume > min_volume  # 使用成交量而非成交金额
            and x.SecurityReference.IPODate is not None
            and x.SecurityReference.IPODate <= min_ipo_date
        ]


    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        精选阶段: 行业和财务筛选
        
        处理流程:
        1. 按行业分组并选取龙头股
        2. 应用财务指标筛选
        3. 记录选股结果和统计信息
        
        参数:
            fine: 精选数据列表
            
        返回:
            List[Symbol]: 最终选中的股票代码列表
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols
            
        # 重置选股标志
        self.selection_on = False 
        self.fine_selection_count += 1
        self.algorithm.Debug(f"===== 第【{self.fine_selection_count}】次选股 =====")
        
        # 步骤1: 应用宽松的财务筛选条件
        financially_filtered, financial_stats = self._apply_financial_filters(fine)
        
        # 步骤2: 计算并筛选波动率
        volatility_filtered, volatility_stats = self._apply_volatility_filter(financially_filtered)
        
        # 步骤3: 按行业分组并使用多层次排序选取股票
        final_stocks = self._group_and_sort_by_sector(volatility_filtered)
        
        # 步骤4: 更新选股结果
        self.last_fine_selected_symbols = [x.Symbol for x in final_stocks]
        
        # 步骤5: 输出选股统计信息
        self._log_selection_results(fine, final_stocks, financial_stats, volatility_stats)
        
        return self.last_fine_selected_symbols


    def _apply_volatility_filter(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        计算并筛选波动率
        
        参数:
            stocks: 通过财务筛选的股票列表
            
        返回:
            Tuple[List[FineFundamental], Dict[str, int]]:
            - 通过波动率筛选的股票列表
            - 波动率筛选统计信息
        """
        filtered_stocks = []
        stats = {'total': len(stocks), 'passed': 0, 'volatility_failed': 0, 'data_missing': 0}
        
        max_volatility = self.config['max_volatility']
        lookback_days = self.config['volatility_lookback_days']
        
        for stock in stocks:
            try:
                # 获取历史价格数据
                history = self.algorithm.History(
                    stock.Symbol, 
                    lookback_days, 
                    Resolution.Daily
                )
                
                if history.empty or len(history) < lookback_days * 0.8:  # 至少80%的数据
                    stats['data_missing'] += 1
                    continue
                
                # 计算日收益率
                closes = history['close']
                returns = closes.pct_change().dropna()
                
                # 计算年化波动率
                volatility = returns.std() * np.sqrt(252)
                
                if volatility <= max_volatility:
                    # 将波动率信息存储在股票对象中供后续使用
                    stock.Volatility = volatility
                    filtered_stocks.append(stock)
                    stats['passed'] += 1
                else:
                    stats['volatility_failed'] += 1
                    
            except Exception as e:
                self.algorithm.Debug(
                    f"[UniverseSelection] 波动率计算错误 {stock.Symbol}: {type(e).__name__}"
                )
                stats['data_missing'] += 1
        
        return filtered_stocks, stats


    def _group_and_sort_by_sector(self, stocks: List[FineFundamental]) -> List[FineFundamental]:
        """
        按行业分组并使用多层次排序选取股票
        
        排序优先级:
        1. 波动率升序（优先选择稳定的）
        2. 成交量降序（优先选择流动性好的）
        
        参数:
            stocks: 通过波动率筛选的股票列表
            
        返回:
            List[FineFundamental]: 最终选中的股票列表
        """
        # 使用defaultdict高效分组
        sector_groups = defaultdict(list)
        valid_sector_codes = set(self.sector_name_to_code.values())
        
        # 按行业分组
        for stock in stocks:
            sector_code = stock.AssetClassification.MorningstarSectorCode
            if sector_code in valid_sector_codes:
                # 确保有成交量数据
                if hasattr(stock, 'Volatility'):  # 确保有波动率数据
                    sector_groups[sector_code].append(stock)
        
        # 多层次排序并选取
        max_per_sector = self.config['max_stocks_per_sector']
        all_selected = []
        
        for sector_code, sector_stocks in sector_groups.items():
            # 多层次排序：先按波动率升序，再按成交量降序
            sorted_stocks = sorted(
                sector_stocks,
                key=lambda x: (x.Volatility, -x.Volume)  # 波动率升序，成交量降序
            )
            
            # 选取前N只
            selected = sorted_stocks[:max_per_sector]
            all_selected.extend(selected)
            
            # 记录每个行业的选择情况
            if selected:
                sector_name = self.sector_code_to_name.get(sector_code, "Unknown")
                self.algorithm.Debug(
                    f"[UniverseSelection] {sector_name}行业: "
                    f"候选{len(sector_stocks)}只 → 选择{len(selected)}只"
                )
        
        return all_selected


    def _apply_financial_filters(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        应用财务筛选条件
        
        参数:
            stocks: 待筛选的股票列表
            
        返回:
            Tuple[List[FineFundamental], Dict[str, int]]: 
            - 通过筛选的股票列表
            - 筛选统计信息字典
        """
        filtered_stocks = []
        
        # 初始化统计计数器
        stats = defaultdict(int, total=len(stocks), passed=0)
        
        # 逐只股票进行财务检查
        for stock in stocks:
            passed, fail_reasons = self._check_financial_criteria(stock)
            
            if passed:
                filtered_stocks.append(stock)
                stats['passed'] += 1
            else:
                # 更新失败原因统计
                for reason in fail_reasons:
                    stats[reason] += 1
        
        return filtered_stocks, stats


    def _check_financial_criteria(self, stock: FineFundamental) -> Tuple[bool, List[str]]:
        """
        检查单只股票的财务指标
        
        使用配置化的财务筛选条件进行检查, 支持动态属性访问
        
        参数:
            stock: 待检查的股票
            
        返回:
            Tuple[bool, List[str]]: 
            - 是否通过所有检查
            - 失败原因列表
        """
        fail_reasons = []
        
        # 首先检查数据完整性
        if not stock.ValuationRatios or not stock.OperationRatios:
            return False, ['data_missing']
        
        # 检查每个财务指标
        for reason, criteria in self.FINANCIAL_CRITERIA.items():
            try:
                # 动态导航属性路径
                value = stock
                for attr in criteria['attr_path']:
                    value = getattr(value, attr, None)
                    if value is None:
                        break
                
                # 评估筛选条件
                if value is None:
                    fail_reasons.append(reason)
                else:
                    threshold = self.config[criteria['config_key']]
                    # 根据操作符类型进行比较
                    if criteria['operator'] == 'lt' and value >= threshold:
                        fail_reasons.append(reason)
                    elif criteria['operator'] == 'gt' and value <= threshold:
                        fail_reasons.append(reason)
                        
            except (AttributeError, TypeError) as e:
                # 记录具体的属性访问错误
                self.algorithm.Debug(
                    f"[UniverseSelection] 财务数据访问错误 "
                    f"{stock.Symbol}.{'.'.join(criteria['attr_path'])}: {type(e).__name__}"
                )
                fail_reasons.append('data_missing')
                break
        
        return len(fail_reasons) == 0, fail_reasons


    def _log_selection_results(self, initial: List[FineFundamental], 
                              final: List[FineFundamental], 
                              financial_stats: Dict[str, int],
                              volatility_stats: Dict[str, int]):
        """
        输出选股结果日志
        
        包含:
        1. 基础统计信息
        2. 财务筛选失败原因分析
        3. 波动率筛选统计
        4. 行业分布统计
        5. 最终选股结果样本
        
        参数:
            initial: 初始候选股票列表
            final: 最终通过筛选的股票列表
            financial_stats: 财务筛选统计信息
            volatility_stats: 波动率筛选统计信息
        """
        # 整体筛选流程统计
        self.algorithm.Debug(
            f"[UniverseSelection] 筛选流程: "
            f"初始{len(initial)}只 → "
            f"财务筛选{financial_stats['passed']}只 → "
            f"波动率筛选{volatility_stats['passed']}只 → "
            f"最终选择{len(final)}只"
        )
        
        # 财务筛选失败原因分析
        financial_filtered = financial_stats['total'] - financial_stats['passed']
        if financial_filtered > 0:
            reason_map = {
                'pe_failed': 'PE',
                'roe_failed': 'ROE',
                'debt_failed': '债务',
                'leverage_failed': '杠杆',
                'data_missing': '缺失'
            }
            
            failed_details = [
                f"{label}({financial_stats[reason]})"
                for reason, label in reason_map.items()
                if financial_stats.get(reason, 0) > 0
            ]
            
            if failed_details:
                self.algorithm.Debug(
                    f"[UniverseSelection] 财务过滤明细: {' '.join(failed_details)}"
                )
        
        # 波动率筛选统计
        volatility_filtered = volatility_stats['total'] - volatility_stats['passed']
        if volatility_filtered > 0:
            self.algorithm.Debug(
                f"[UniverseSelection] 波动率过滤: "
                f"高波动{volatility_stats.get('volatility_failed', 0)}只, "
                f"数据缺失{volatility_stats.get('data_missing', 0)}只"
            )
        
        # 行业分布统计
        if final:
            sector_counts = defaultdict(int)
            volatility_by_sector = defaultdict(list)
            
            # 统计每个行业的股票数量和平均波动率
            for stock in final:
                sector_code = stock.AssetClassification.MorningstarSectorCode
                sector_name = self.sector_code_to_name.get(sector_code)
                if sector_name:
                    sector_counts[sector_name] += 1
                    if hasattr(stock, 'Volatility'):
                        volatility_by_sector[sector_name].append(stock.Volatility)
            
            if sector_counts:
                # 按数量降序排列
                sector_items = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)
                sector_details = []
                for name, count in sector_items:
                    avg_vol = np.mean(volatility_by_sector[name]) if volatility_by_sector[name] else 0
                    sector_details.append(f"{name}({count}只,波动率{avg_vol:.1%})")
                
                self.algorithm.Debug(f"[UniverseSelection] 行业分布: {' '.join(sector_details)}")
        
        # 最终选股结果
        final_count = len(final)
        if final_count > 0:
            # 显示前5只股票作为样本（包含波动率信息）
            sample_info = []
            for stock in final[:5]:
                vol = getattr(stock, 'Volatility', 0)
                sample_info.append(f"{stock.Symbol.Value}({vol:.1%})")
            
            self.algorithm.Debug(
                f"[UniverseSelection] 选股完成: "
                f"共{final_count}只股票, 前5样本: {sample_info}"
            )
        else:
            self.algorithm.Debug("[UniverseSelection] 选股完成: 无符合条件的股票")