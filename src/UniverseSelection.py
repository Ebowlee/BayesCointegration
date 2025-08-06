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
    贝叶斯协整策略的股票选择模型
    
    该模型继承自QuantConnect的FineFundamentalUniverseSelectionModel，负责从美股市场中
    筛选出适合进行配对交易的股票池。筛选过程分为粗选(Coarse)和精选(Fine)两个阶段。
    
    工作流程:
    1. 粗选阶段(_select_coarse):
       - 筛选有基本面数据的股票
       - 价格门槛: 剔除低价股避免流动性风险
       - 成交量门槛: 确保足够的流动性支持配对交易
       - IPO时间: 剔除新上市股票，确保历史数据充足
    
    2. 精选阶段(_select_fine):
       - 财务指标筛选: PE、ROE、债务率、杠杆率
       - 波动率筛选: 剔除波动过大的股票
       - 行业分组: 确保每个行业都有足够的候选股票
       - 市值排序: 每个行业选取流动性最好的龙头股
    
    配置参数(通过config字典传入):
    - max_stocks_per_sector: 每个行业最多选择的股票数量
    - min_price: 最低股价要求(美元)
    - min_volume: 最低日成交量要求(股)
    - min_days_since_ipo: 最短上市时间要求(天)
    - max_pe: 最高市盈率
    - min_roe: 最低净资产收益率
    - max_debt_ratio: 最高资产负债率
    - max_leverage_ratio: 最高财务杠杆
    - max_volatility: 最高年化波动率
    - volatility_lookback_days: 波动率计算回望天数
    
    使用方式:
    在主策略中通过SetUniverseSelection()设置，并通过Schedule定期调用TriggerSelection()
    触发选股。选股结果会传递给AlphaModel进行配对分析。
    
    注意事项:
    - 选股频率建议每月一次，避免过于频繁导致配对不稳定
    - 财务数据可能存在缺失，需要适当的容错处理
    - 行业分类基于Morningstar标准，共8个主要行业
    """
    
    # 财务筛选条件配置字典
    # 定义了需要检查的财务指标及其筛选逻辑，用于_check_financial_criteria方法
    FINANCIAL_CRITERIA = {
        'pe_failed': {
            'attr_path': ['ValuationRatios', 'PERatio'],      # 市盈率(P/E Ratio)的对象属性访问路径
            'config_key': 'max_pe',                            # 在config中对应的配置键名
            'operator': 'lt'                                   # 比较操作符: 'lt'表示值必须小于阈值才通过
            # 业务含义: 市盈率反映投资回收期，过高的PE可能意味着股票被高估
        },
        'roe_failed': {
            'attr_path': ['OperationRatios', 'ROE', 'Value'], # 净资产收益率(ROE)的访问路径，注意需要.Value属性
            'config_key': 'min_roe',                           # 对应的最小ROE配置
            'operator': 'gt'                                   # 'gt'表示值必须大于阈值才通过
            # 业务含义: ROE衡量公司盈利能力，低ROE可能表示经营效率差
        },
        'debt_failed': {
            'attr_path': ['OperationRatios', 'DebtToAssets', 'Value'],  # 资产负债率的访问路径
            'config_key': 'max_debt_ratio',                              # 最大负债率配置
            'operator': 'lt'                                             # 值必须小于阈值
            # 业务含义: 资产负债率反映财务风险，过高的负债率增加破产风险
        },
        'leverage_failed': {
            'attr_path': ['OperationRatios', 'FinancialLeverage', 'Value'],  # 财务杠杆的访问路径
            'config_key': 'max_leverage_ratio',                               # 最大杠杆率配置
            'operator': 'lt'                                                  # 值必须小于阈值
            # 业务含义: 财务杠杆=总资产/股东权益，过高杠杆增加财务风险
        }
    }
    

    def __init__(self, algorithm, config, sector_code_to_name, sector_name_to_code):
        """
        初始化选股模型
        
        参数:
            algorithm (QCAlgorithm): 主算法实例，用于访问历史数据和输出日志
            config (dict): 选股配置字典，必须包含以下键:
                - max_stocks_per_sector (int): 每个行业最多选择股票数
                - min_price (float): 最低股价(美元)
                - min_volume (float): 最低日成交量(股数)
                - min_days_since_ipo (int): 最短上市天数
                - max_pe (float): 最高市盈率
                - min_roe (float): 最低净资产收益率
                - max_debt_ratio (float): 最高资产负债率
                - max_leverage_ratio (float): 最高财务杠杆
                - max_volatility (float): 最高年化波动率
                - volatility_lookback_days (int): 波动率计算回望天数
                - volatility_data_completeness_ratio (float): 波动率计算时要求的最低数据完整性比例
            sector_code_to_name (dict): Morningstar行业代码到名称的映射字典
                示例: {MorningstarSectorCode.Technology: "Technology"}
            sector_name_to_code (dict): 行业名称到Morningstar代码的映射字典
                示例: {"Technology": MorningstarSectorCode.Technology}
        """
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name
        self.sector_name_to_code = sector_name_to_code
        
        # 状态管理
        self.selection_on = False                                                   # 是否触发选股
        self.last_fine_selected_symbols = []                                        # 上次选股结果
        self.fine_selection_count = 0                                               # 选股次数统计
        
        algorithm.Debug("[UniverseSelection] 初始化完成")
        super().__init__(self._select_coarse, self._select_fine)


    def TriggerSelection(self):
        """
        触发新一轮选股的外部接口
        
        该方法通常由主策略通过Schedule定期调用（如每月第一个交易日）。
        调用后，下一次粗选和精选将执行完整的筛选流程，而非返回缓存结果。
        
        使用示例:
            # 在主策略的Initialize中设置
            self.Schedule.On(self.DateRules.MonthStart(), 
                           self.TimeRules.At(9, 10), 
                           Action(self.universe_selector.TriggerSelection))
        """
        self.selection_on = True


    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段: 对全市场股票进行基础筛选
        
        该方法由QuantConnect框架自动调用，接收的参数实际上是一个迭代器。
        筛选逻辑采用短路求值优化性能。
        
        筛选条件（按检查顺序）:
        1. HasFundamentalData: 必须有基本面数据（排除ETF、期权等）
        2. Price > min_price: 股价高于最低要求，避免低价股流动性风险
        3. Volume > min_volume: 日成交量高于最低要求，确保流动性
        4. IPO日期检查: 上市时间超过min_days_since_ipo天，确保历史数据充足
        
        参数:
            coarse (IEnumerable[CoarseFundamental]): QuantConnect提供的粗选数据迭代器
                包含所有美股的基础信息（价格、成交量、市值等）
            
        返回:
            List[Symbol]: 通过初步筛选的股票Symbol列表
                - 触发选股时: 返回筛选结果供精选阶段使用
                - 未触发时: 返回上次选股结果的Symbol列表以保持稳定
        
        性能优化:
            - 使用列表推导式和短路求值
            - 预计算所有阈值避免重复计算
            - 条件按淘汰率排序，尽早过滤
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols
        
        # 将迭代器转换为列表
        # QuantConnect传入的是OfTypeIterator，需要转换才能使用len()等列表操作
        coarse = list(coarse)
        
        # 预计算所有筛选阈值，避免在循环中重复计算
        min_ipo_date = self.algorithm.Time - timedelta(days=self.config['min_days_since_ipo'])
        min_price = self.config['min_price']
        min_volume = self.config['min_volume']
        
        # 使用列表推导式进行高效筛选
        # 条件顺序经过优化：先检查计算成本低的条件，利用Python的短路求值特性
        return [
            x.Symbol for x in coarse 
            if x.HasFundamentalData                         # 排除ETF、期权等非股票资产
            and x.Price > min_price                         # 价格筛选：避免penny stocks
            and x.Volume > min_volume                       # 成交量筛选：确保流动性（注：Volume是股数而非金额）
            and x.SecurityReference.IPODate is not None     # 确保IPO日期数据存在
            and x.SecurityReference.IPODate <= min_ipo_date # IPO时间筛选：需要足够的历史数据
        ]


    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        精选阶段: 对粗选结果进行财务、波动率和行业筛选
        
        该方法是选股的核心逻辑，通过多层筛选确保选出的股票：
        1. 财务健康（盈利能力强、负债合理）
        2. 波动适中（适合配对交易）
        3. 行业分散（降低系统性风险）
        
        处理流程:
        1. 财务筛选: 检查PE、ROE、负债率、杠杆率等关键指标
        2. 波动率筛选: 计算历史波动率，剔除过于波动的股票
        3. 行业分组: 按Morningstar行业分类，每个行业选取市值最大的N只
        4. 结果记录: 更新选股结果并输出详细统计日志
        
        参数:
            fine (IEnumerable[FineFundamental]): QuantConnect提供的精选数据迭代器
                包含通过粗选的股票的详细财务数据
            
        返回:
            List[Symbol]: 最终入选的股票Symbol列表
                - 数量: 通常20-100只，取决于市场状况和筛选参数
                - 分布: 覆盖多个行业，每个行业不超过max_stocks_per_sector只
        
        注意事项:
            - 财务数据可能缺失或异常，需要容错处理
            - 选股结果会缓存到self.last_fine_selected_symbols
            - 每次选股都会输出详细的统计信息用于监控
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols
            
        # 重置选股标志
        self.selection_on = False 
        self.fine_selection_count += 1
        self.algorithm.Debug(f"===== 第【{self.fine_selection_count}】次选股 =====")
        
        # 将迭代器转换为列表，确保后续操作（如len()）可以正常执行
        fine = list(fine)
        
        # 步骤1: 应用财务筛选
        # 筛选PE、ROE、负债率、杠杆率等财务指标，确保公司基本面健康
        financially_filtered, financial_stats = self._apply_financial_filters(fine)
        
        # 步骤2: 计算并筛选波动率
        # 使用历史价格数据计算年化波动率，剔除波动过大的股票
        volatility_filtered, volatility_stats = self._apply_volatility_filter(financially_filtered)
        
        # 步骤3: 行业分组和排序
        # 按Morningstar行业分类，每个行业选取市值最大的N只股票，确保行业分散
        final_stocks = self._group_and_sort_by_sector(volatility_filtered)
        
        # 步骤4: 缓存选股结果
        # 保存本次选股结果，供非触发期返回使用
        self.last_fine_selected_symbols = [x.Symbol for x in final_stocks]
        
        # 步骤5: 输出详细的选股统计
        # 包括各阶段筛选数量、失败原因分析、行业分布等信息
        self._log_selection_results(fine, final_stocks, financial_stats, volatility_stats)
        
        return self.last_fine_selected_symbols


    def _apply_volatility_filter(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        计算历史波动率并筛选出波动适中的股票
        
        波动率是配对交易的重要考量因素：
        - 过高的波动率增加配对风险和回撤
        - 需要足够的历史数据计算可靠的波动率
        
        计算方法:
        1. 获取lookback_days天的日收盘价
        2. 计算日收益率: (price[t] - price[t-1]) / price[t-1]
        3. 年化波动率 = 日收益率标准差 * sqrt(252)
        
        参数:
            stocks (List[FineFundamental]): 通过财务筛选的股票列表
            
        返回:
            Tuple[List[FineFundamental], Dict[str, int]]: 包含两个元素的元组
                - List[FineFundamental]: 通过波动率筛选的股票列表
                - Dict[str, int]: 统计信息字典，包含:
                    - 'total': 输入股票总数
                    - 'passed': 通过筛选的股票数
                    - 'volatility_failed': 因波动率过高被剔除的数量
                    - 'data_missing': 因历史数据不足被剔除的数量
        
        筛选标准:
            - 历史数据完整性 >= 80%（至少0.8 * lookback_days天数据）
            - 年化波动率 < max_volatility（配置参数）
        """
        filtered_stocks = []
        stats = {'total': len(stocks), 'passed': 0, 'volatility_failed': 0, 'data_missing': 0}
        
        max_volatility = self.config['max_volatility']
        lookback_days = self.config['volatility_lookback_days']
        
        # 批量获取所有股票的历史数据以提升性能
        symbols = [stock.Symbol for stock in stocks]
        self.algorithm.Debug(f"[UniverseSelection] 批量获取{len(symbols)}只股票的历史数据...")
        
        try:
            all_history = self.algorithm.History(
                symbols, 
                lookback_days, 
                Resolution.Daily
            )
            
            if all_history.empty:
                self.algorithm.Debug("[UniverseSelection] 批量获取历史数据失败，返回空DataFrame")
                return [], stats
                
        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] 批量获取历史数据异常: {type(e).__name__}: {str(e)}")
            return [], stats
        
        # 处理每只股票
        for stock in stocks:
            try:
                # 从批量数据中提取该股票的历史
                if stock.Symbol in all_history.index.levels[0]:
                    history = all_history.loc[stock.Symbol]
                else:
                    stats['data_missing'] += 1
                    continue
                
                # 数据完整性检查：要求至少有配置比例的交易日数据
                # 避免因停牌、新股等原因导致数据不足影响波动率计算准确性
                min_required_days = lookback_days * self.config['volatility_data_completeness_ratio']
                if history.empty or len(history) < min_required_days:
                    stats['data_missing'] += 1
                    continue
                
                # 计算日收益率
                # pct_change()计算百分比变化: (price[t] - price[t-1]) / price[t-1]
                closes = history['close']
                returns = closes.pct_change().dropna()
                
                # 计算年化波动率
                # 标准做法：日波动率 * sqrt(252)，其中252是美股年均交易日数
                volatility = returns.std() * np.sqrt(252)
                
                # 波动率筛选：只保留波动率适中的股票
                if volatility <= max_volatility:
                    # 将波动率存储为股票属性，供后续日志输出使用
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
        
        该方法确保选股结果的行业分散性，避免集中在某几个行业。
        每个行业最多选择max_stocks_per_sector只股票。
        
        排序逻辑（按优先级）:
        1. 波动率升序: 优先选择波动率低的稳定股票
        2. 市值降序: 在波动率相近时，优先选择大市值龙头股
        
        参数:
            stocks (List[FineFundamental]): 通过财务和波动率筛选的股票列表
            
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
            
            # 行业选股详情已在最后的行业分布中统一输出，避免重复日志
        
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
        
        # 遍历所有财务筛选条件进行检查
        for reason, criteria in self.FINANCIAL_CRITERIA.items():
            try:
                # 动态导航属性路径
                # 例如: ['OperationRatios', 'ROE', 'Value'] 
                # 相当于: stock.OperationRatios.ROE.Value
                value = stock
                for attr in criteria['attr_path']:
                    value = getattr(value, attr, None)
                    if value is None:  # 如果中间任何一级属性不存在，停止导航
                        break
                
                # 评估筛选条件
                if value is None:
                    # 财务数据缺失，记录失败原因
                    fail_reasons.append(reason)
                else:
                    # 从配置中获取阈值
                    threshold = self.config[criteria['config_key']]
                    
                    # 根据操作符进行比较
                    # 'lt' (less than): 值必须小于阈值（如PE < max_pe）
                    # 'gt' (greater than): 值必须大于阈值（如ROE > min_roe）
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