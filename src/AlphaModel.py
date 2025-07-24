# region imports
from AlgorithmImports import *
from datetime import timedelta
import itertools
from collections import defaultdict
from QuantConnect.Data.Fundamental import MorningstarSectorCode
from statsmodels.tsa.stattools import coint
import numpy as np
import pymc as pm
# endregion

class BayesianCointegrationAlphaModel(AlphaModel):
    """
    贝叶斯协整Alpha模型
    
    该模型使用贝叶斯方法和MCMC采样技术构建配对交易策略, 主要功能包括:
    1. 通过Engle-Granger检验识别行业内协整股票对
    2. 使用PyMC进行贝叶斯线性回归, 生成alpha和beta的后验分布
    3. 基于残差z-score判断价格偏离程度, 生成均值回归交易信号
    4. 实现波动率筛选和风险控制, 确保交易对的质量
    
    核心原理: 协整对的残差应平稳且均值为零, 当价格偏离超过阈值时产生交易机会
    """
    
    def __init__(self, algorithm, config, pair_ledger):
        """
        初始化贝叶斯协整Alpha模型
        
        Args:
            algorithm: QuantConnect算法实例, 提供数据和交易接口
            config: 配置字典, 包含所有模型参数和阈值设置
                   - pvalue_threshold: 协整检验p值阈值 (默认0.025)
                   - correlation_threshold: 相关性筛选阈值 (默认0.7)
                   - max_pairs: 最大协整对数量 (默认5)
                   - entry_threshold/exit_threshold: 信号进入/退出阈值
                   - mcmc_*: MCMC采样参数配置
            pair_ledger: 配对记账簿实例
        """
        super().__init__() 
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        self.is_universe_selection_on = False
        self.symbols = []

        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']
        self.lookback_period = config['lookback_period']
        self.mcmc_burn_in = config['mcmc_burn_in']
        self.mcmc_draws = config['mcmc_draws']
        self.mcmc_chains = config['mcmc_chains']
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.upper_limit = config['upper_limit']
        self.lower_limit = config['lower_limit']
        
        self.max_volatility_3month = config['max_volatility_3month']
        self.volatility_lookback_days = config['volatility_lookback_days']
        self.selection_interval_days = config.get('selection_interval_days', 30)
        self.dynamic_update_enabled = config.get('dynamic_update_enabled', True)
        self.min_beta_threshold = config.get('min_beta_threshold', 0.2)
        self.max_beta_threshold = config.get('max_beta_threshold', 3.0)
        
        # 信号有效期配置
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 1)
        self.entry_signal_duration_days = config.get('entry_signal_duration_days', 2)
        
        # 历史数据缓存
        # 数据结构: {Symbol: pandas.DataFrame}
        # DataFrame包含列: ['open', 'high', 'low', 'close', 'volume'] 
        # 索引: DatetimeIndex
        # 示例: self.historical_data_cache[Symbol("AAPL")] = DataFrame with 252 days of price data
        self.historical_data_cache = {}
        
        # 数据质量分类:根据数据质量将symbols分类
        self.data_quality = {
            'basic_valid': set(),          # 基础数据可用(有数据,无空值)
            'volatility_ready': set(),     # 波动率计算就绪(足够的波动率计算天数)
            'cointegration_ready': set(),  # 协整检验就绪(足够的协整检验天数,价格合理)
            'full_modeling_ready': set()   # 完整建模就绪(满足所有建模要求)
        }
        
        # 动态贝叶斯更新:存储历史后验分布用作下轮先验
        self.historical_posteriors = {}  # {(symbol1, symbol2): {'alpha_samples': array, 'beta_samples': array, 'sigma_samples': array, 'update_time': datetime}}
        self.dynamic_update_lookback_days = self.selection_interval_days  # 动态更新时使用的最近数据天数
        
        # 动态间隔计算:记录上次选股日期
        self.last_selection_date = None

        self.algorithm.Debug(f"[AlphaModel] 初始化完成 (最大{self.max_pairs}对, 波动率<{self.max_volatility_3month:.0%})")



    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        """
        Alpha模型核心更新方法, 执行协整检验和信号生成
        
        该方法分为两个阶段:
        1. 选股日: 进行批量协整检验和贝叶斯建模 (月度执行)
        2. 交易日: 计算z-score并生成交易信号 (每日执行)
        
        Args:
            algorithm: QuantConnect算法实例
            data: 当前时间片的市场数据
            
        Returns:
            List[Insight]: 生成的交易信号洞察列表, 每个协整对产生两个相反方向的信号
        """
        Insights = []

        if not self.symbols or len(self.symbols) < 2:
            self.algorithm.Debug("[AlphaModel] 当前未接受到足够数量的选股")
            return Insights
        
        # 选股日执行协整检验和贝叶斯建模
        if self.is_universe_selection_on:
            
            # 计算实际选股间隔
            actual_interval_days = self.selection_interval_days
            if self.last_selection_date is not None:
                actual_interval_days = (self.algorithm.Time - self.last_selection_date).days
                self.algorithm.Debug(f"[AlphaModel] 实际选股间隔: {actual_interval_days}天 (上次: {self.last_selection_date.strftime('%Y-%m-%d')})")
            
            if self.historical_posteriors:
                keys_list = list(self.historical_posteriors.keys())[:5]  # 显示前5个键
            self.industry_cointegrated_pairs = {}
            self.posterior_params = {}
            
            # 更新动态间隔天数
            self.dynamic_update_lookback_days = actual_interval_days
            self.last_selection_date = self.algorithm.Time
            
            self._BatchLoadHistoricalData(self.symbols)
            
            volatility_filtered_symbols = self._VolatilityFilter(self.symbols)
            
            sector_to_symbols = self.GetIntraIndustryPairs(volatility_filtered_symbols)

            # 收集行业协整对信息
            sector_pairs_info = {}
            
            for sector, symbols in sector_to_symbols.items():
                # 每个行业内进行组合配对, 逐个进行协整检验
                pairs = list(itertools.combinations(symbols, 2))
                intra_industry_pairs = {}

                for pair_tuple in pairs:
                    symbol1, symbol2 = pair_tuple
                    cointegrated_pair = self.CointegrationTestForSinglePair(symbol1, symbol2)
                    intra_industry_pairs.update(cointegrated_pair)
            
                filtered_intra_industry_pairs = self.FilterCointegratedPairs(intra_industry_pairs)
                
                # 记录该行业的协整对信息
                if filtered_intra_industry_pairs:
                    pair_names = [f"{s1.Value}-{s2.Value}" for (s1, s2) in filtered_intra_industry_pairs.keys()]
                    sector_pairs_info[sector] = {
                        'count': len(filtered_intra_industry_pairs),
                        'pairs': pair_names
                    }
                
                self.industry_cointegrated_pairs.update(filtered_intra_industry_pairs)

            # 输出行业协整对汇总
            if sector_pairs_info:
                summary_parts = []
                for sector, info in sector_pairs_info.items():
                    summary_parts.append(f"{sector}({info['count']})")
                
                self.algorithm.Debug(f"[AlphaModel] 行业协整对: {' '.join(summary_parts)}")
            
            # 处理所有协整对:建立后验参数
            success_count = 0
            dynamic_update_count = 0
            new_modeling_count = 0
            
            # 收集按建模方式分类的协整对信息
            dynamic_update_pairs = []
            new_modeling_pairs = []
            
            for pair_key, pair_info in self.industry_cointegrated_pairs.items():
                symbol1, symbol2 = pair_key
                posterior_param = None
                
                # 尝试动态更新(如果启用且存在历史后验)
                if self.dynamic_update_enabled:
                    # 检查正向和反向键
                    if pair_key in self.historical_posteriors:
                        # 使用正向键,保持当前顺序
                        prior_params = self.historical_posteriors[pair_key]
                        posterior_param = self.PyMCModel(
                            symbol1, symbol2,
                            use_prior=True,
                            data_range='recent',
                            prior_params=prior_params
                        )
                    elif (symbol2, symbol1) in self.historical_posteriors:
                        # 使用反向键,保持历史顺序
                        prior_params = self.historical_posteriors[(symbol2, symbol1)]
                        posterior_param = self.PyMCModel(
                            symbol2, symbol1,
                            use_prior=True,
                            data_range='recent',
                            prior_params=prior_params
                        )
                    
                    if posterior_param is not None:
                        dynamic_update_count += 1
                        dynamic_update_pairs.append(f"{symbol1.Value}-{symbol2.Value}")
                
                # 如果动态更新失败或不适用,使用完整建模
                if posterior_param is None:
                    posterior_param = self.PyMCModel(symbol1, symbol2)
                    if posterior_param is not None:
                        new_modeling_count += 1
                        new_modeling_pairs.append(f"{symbol1.Value}-{symbol2.Value}")
                
                # 保存成功的结果
                if posterior_param is not None:
                    self.posterior_params[pair_key] = posterior_param
                    success_count += 1
            
            # 统计和日志输出
            total_pairs = len(self.industry_cointegrated_pairs)
            failed_count = total_pairs - success_count
            
            # 优化的建模统计
            self.algorithm.Debug(f"[AlphaModel] 建模统计: 动态更新{dynamic_update_count}对, 完整建模{new_modeling_count}对, 总成功{success_count}对")
            
            # 按建模方式分类显示协整对
            if dynamic_update_pairs:
                self.algorithm.Debug(f"[AlphaModel] 动态更新: {', '.join(dynamic_update_pairs)}")
            if new_modeling_pairs:
                self.algorithm.Debug(f"[AlphaModel] 完整建模: {', '.join(new_modeling_pairs)}")
            
            # 将成功建模的协整对更新到配对记账簿
            successful_pairs = [(symbol1, symbol2) for (symbol1, symbol2) in self.posterior_params.keys()]
            self.pair_ledger.update_pairs(successful_pairs)
            
            self.is_universe_selection_on = False

        # 每日执行的信号生成逻辑
        # 遍历所有成功建模的协整对, 计算当前价格偏离并生成交易信号
        for pair_key in self.posterior_params.keys():
            symbol1, symbol2 = pair_key
            posterior_param = self.posterior_params[pair_key]
            posterior_param_with_zscore = self.CalculateResidualZScore(symbol1, symbol2, data, posterior_param)
            signal = self.GenerateSignals(symbol1, symbol2, posterior_param_with_zscore)
            Insights.extend(signal)
        
        # 信号生成日志
        # No longer output signal count to reduce log volume
        return Insights    



    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        证券变更事件回调, 同步更新内部股票池
        
        当UniverseSelection模块更新股票池时, 框架自动调用此方法.
        设置选股标志位触发下次Update时的协整检验流程.
        
        Args:
            algorithm: QuantConnect算法实例
            changes: 证券变更信息, 包含新增和移除的股票列表
        """
        self.is_universe_selection_on = True

        for added_security in changes.AddedSecurities:
            symbol = added_security.Symbol
            if symbol and symbol not in self.symbols:
                self.symbols.append(symbol)
    
        for removed_security in changes.RemovedSecurities:
            symbol = removed_security.Symbol
            if symbol and symbol in self.symbols:
                self.symbols.remove(symbol)
    
    

    def _BatchLoadHistoricalData(self, symbols: list[Symbol]):
        """
        批量加载历史数据并进行全面的数据质量检查和分类
        
        使用单次History API调用获取所有股票数据, 并根据不同用途的数据质量要求
        对symbols进行分类, 避免后续方法中的重复检查.
        
        数据质量分类:
        - basic_valid: 基础数据可用(有数据,无空值)
        - volatility_ready: 波动率计算就绪
        - cointegration_ready: 协整检验就绪  
        - full_modeling_ready: 完整建模就绪
        
        Args:
            symbols: 需要获取历史数据的股票符号列表
        """
        # 重置基础数据质量分类 (仅基础检查)
        self.data_quality = {
            'basic_valid': set(),      # 存在性, 完整性通过
            'data_complete': set(),    # 数据长度充足  
            'price_valid': set()       # 价格数据合理
        }
        
        try:
            # 一次性获取所有股票的历史数据 (性能优化)
            all_histories = self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
            self.historical_data_cache = {}
            
            data_stats = {
                'total': len(symbols),
                'basic_valid': 0,
                'data_complete': 0,
                'price_valid': 0,
                'failed': 0
            }
            
            for symbol in symbols:
                try:
                    # 1. 基础存在性检查
                    if symbol not in all_histories.index.get_level_values(0):
                        data_stats['failed'] += 1
                        continue
                        
                    symbol_data = all_histories.loc[symbol]
                    if symbol_data.empty:
                        data_stats['failed'] += 1
                        continue
                        
                    # 2. 数据完整性检查 (填充缺失值)
                    close_prices = symbol_data['close']
                    if close_prices.isnull().any():
                        close_prices = close_prices.fillna(method='pad').fillna(method='bfill')
                        if close_prices.isnull().any():
                            data_stats['failed'] += 1
                            continue
                    
                    # 更新缓存为处理后的数据
                    symbol_data['close'] = close_prices
                    self.historical_data_cache[symbol] = symbol_data
                    
                    # 3. 基础数据质量达标
                    self.data_quality['basic_valid'].add(symbol)
                    data_stats['basic_valid'] += 1
                    
                    # 4. 数据长度完整性检查 (至少95%的期望数据,确保协整检验统计有效性)
                    if len(close_prices) >= int(self.lookback_period * 0.98):
                        self.data_quality['data_complete'].add(symbol)
                        data_stats['data_complete'] += 1
                    
                    # 5. 价格合理性检查 (正数, 无异常值)
                    if (close_prices > 0).all():
                        self.data_quality['price_valid'].add(symbol)
                        data_stats['price_valid'] += 1
                        
                except Exception as e:
                    data_stats['failed'] += 1
            
            # 简化的数据质量统计
            self.algorithm.Debug(f"[AlphaModel] 数据统计: {data_stats['total']}只 → 有效{data_stats['price_valid']}只")
                        
        except (KeyError, ValueError, IndexError) as e:
            self.historical_data_cache = {}
        except Exception as e:
            self.historical_data_cache = {}



    def _VolatilityFilter(self, symbols: list[Symbol]) -> list[Symbol]:
        """
        基于年化波动率进行股票筛选
        
        结合基础数据质量检查和波动率业务逻辑:
        1. 使用基础数据质量确保数据可用性  
        2. 计算波动率并应用业务阈值判断
        3. 过滤高波动股票以提高配对稳定性
        
        Args:
            symbols: 候选股票符号列表
            
        Returns:
            list[Symbol]: 通过波动率筛选的股票列表
        """
        filtered_symbols = []
        volatility_failed = 0
        data_missing = 0
        
        for symbol in symbols:
            try:
                # 1. 基础数据质量门槛检查
                if symbol not in self.data_quality['price_valid']:
                    data_missing += 1
                    continue
                
                # 2. 业务逻辑:波动率计算和阈值判断
                price_data = self.historical_data_cache[symbol]['close']
                recent_data = price_data.tail(self.volatility_lookback_days)
                
                # 确保有足够数据进行波动率计算
                if len(recent_data) >= self.volatility_lookback_days:
                    returns = recent_data.pct_change().dropna()
                    
                    if len(returns) > 0:
                        daily_volatility = returns.std()
                        # 年化波动率 = 日波动率 × √252 (交易日年化)
                        annual_volatility = daily_volatility * np.sqrt(252)
                        
                        # 业务阈值判断
                        if annual_volatility <= self.max_volatility_3month:
                            filtered_symbols.append(symbol)
                        else:
                            volatility_failed += 1
                    else:
                        data_missing += 1
                else:
                    data_missing += 1
                    
            except Exception as e:
                self.algorithm.Debug(f"[AlphaModel] 波动率计算异常 {symbol.Value}: {str(e)[:30]}")
                data_missing += 1
        
        # 详细的筛选统计输出
        total_input = len(symbols)
        total_filtered = len(filtered_symbols)
        
        details = []
        if volatility_failed > 0:
            details.append(f"波动率过滤{volatility_failed}只")
        if data_missing > 0:
            details.append(f"数据缺失{data_missing}只")
        
        detail_msg = f" ({', '.join(details)})" if details else ""
        self.algorithm.Debug(f"[AlphaModel] 波动率筛选: 候选{total_input}只 → 通过{total_filtered}只{detail_msg}")
            
        return filtered_symbols

    def GetIntraIndustryPairs(self, symbols: list[Symbol]) -> dict:
        """
        按行业分组股票, 为同行业配对做准备
        
        协整关系在同行业内更为稳定, 跨行业配对容易受到行业轮动影响.
        支持8个主要Morningstar行业分类.
        
        Args:
            symbols: 输入股票列表
            
        Returns:
            dict: 行业名称到股票列表的映射字典
                 格式: {"Technology": [AAPL, MSFT], "Healthcare": [JNJ, PFE]}
        """
        sector_map = {
                "Technology": MorningstarSectorCode.Technology,
                "Healthcare": MorningstarSectorCode.Healthcare,
                "Energy": MorningstarSectorCode.Energy,
                "ConsumerDefensive": MorningstarSectorCode.ConsumerDefensive,
                "ConsumerCyclical": MorningstarSectorCode.ConsumerCyclical,
                "CommunicationServices": MorningstarSectorCode.CommunicationServices,
                "Industrials": MorningstarSectorCode.Industrials,
                "Utilities": MorningstarSectorCode.Utilities
                }
        
        # 创建反向映射: 从MorningstarSectorCode值到字符串key
        reverse_sector_map = {v: k for k, v in sector_map.items()}
        sector_to_symbols = defaultdict(list)
        # 分类 symbols 到对应行业
        for symbol in symbols:
            security = self.algorithm.Securities[symbol]
            sector = security.Fundamentals.AssetClassification.MorningstarSectorCode
            if sector in sector_map.values():
                sector_name = reverse_sector_map[sector]
                sector_to_symbols[sector_name].append(symbol)
        return sector_to_symbols



    def CointegrationTestForSinglePair(self, symbol1, symbol2):
        """
        对单个股票对执行Engle-Granger协整检验
        
        信任基础数据质量保证,专注协整检验的业务逻辑:
        1. 基础数据质量门槛检查 (信任_BatchLoadHistoricalData的98%长度保证)
        2. 相关性预筛选 (业务逻辑)
        3. Engle-Granger统计检验 (核心业务)
        
        Args:
            symbol1, symbol2: 待检验的股票对
            
        Returns:
            dict: 协整对信息字典, 空字典表示未通过检验
                 格式: {(symbol1, symbol2): {"pvalue": 0.01, "critical_values": [...]}}
        """
        cointegrated_pair = {}
        
        try:
            # 1. 基础数据质量门槛检查(信任data_complete已保证80%长度要求)
            if (symbol1 not in self.data_quality['data_complete'] or 
                symbol2 not in self.data_quality['data_complete']):
                return cointegrated_pair
            
            # 2. 直接获取预处理的价格数据(质量已保证)
            price1 = self.historical_data_cache[symbol1]['close']
            price2 = self.historical_data_cache[symbol2]['close']
            
            # 3. 业务逻辑:相关性预筛选 - 相关性过低不太可能存在协整关系
            correlation = price1.corr(price2)
            if abs(correlation) < self.correlation_threshold:
                return cointegrated_pair
            
            # 4. 核心业务:Engle-Granger协整检验 - 检验价格序列线性组合是否平稳
            score, pvalue, critical_values = coint(price1, price2)
            is_cointegrated = pvalue < self.pvalue_threshold

            if is_cointegrated:
                pair_key = (symbol1, symbol2)
                cointegrated_pair[pair_key] = {
                    'pvalue': pvalue,
                    'critical_values': critical_values,
                    'create_time': self.algorithm.Time
                }
                
        except (KeyError, ValueError, ImportError):
            return None
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 协整检验异常 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")

        return cointegrated_pair
    


    def FilterCointegratedPairs(self, cointegrated_pairs) -> dict:
        """
        按质量筛选协整对, 控制投资组合规模
        
        筛选流程:
        1. p值排序:选择最强协整关系的股票对
        2. 重复限制:限制每只股票的重复使用次数
        3. 数量控制:限制最大协整对数量
        
        Args:
            cointegrated_pairs: 原始协整对字典
            
        Returns:
            dict: 筛选后的协整对字典, 数量受max_pairs参数限制
        """
        # 按p值排序, 选择最强协整关系的股票对
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = {}
        
        for keys, values in sorted_pairs:
            s1, s2 = keys
            # 避免单一股票在多个配对中重复使用, 降低集中度风险
            if symbol_count[s1] <= self.max_symbol_repeats and symbol_count[s2] <= self.max_symbol_repeats:
                filtered_pairs[keys] = values
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) >= self.max_pairs:
                break
                
        return filtered_pairs
    



    def _GetRecentData(self, symbol1: Symbol, symbol2: Symbol, days: int = None) -> tuple:
        """
        获取指定天数的最近价格数据用于动态更新
        
        信任基础数据质量保证,专注动态数据提取的业务逻辑:
        1. 基础数据质量门槛检查 (信任_BatchLoadHistoricalData结果)
        2. 动态数据范围验证 (业务逻辑:是否有足够的最近数据)
        
        Args:
            symbol1, symbol2: 协整对股票
            days: 回看天数, None时使用动态计算的间隔天数
            
        Returns:
            tuple: (recent_price1, recent_price2) 或 (None, None) 如果数据不足
        """
        # 使用动态计算的间隔天数
        if days is None:
            days = self.dynamic_update_lookback_days
        
        try:
            # 1. 基础数据质量门槛检查(信任批量加载的质量保证)
            if (symbol1 not in self.data_quality['data_complete'] or 
                symbol2 not in self.data_quality['data_complete']):
                return None, None
            
            # 2. 直接获取预处理的缓存数据(质量已保证)
            price1_full = self.historical_data_cache[symbol1]['close']
            price2_full = self.historical_data_cache[symbol2]['close']
            
            # 3. 业务逻辑验证:动态数据范围是否足够
            if len(price1_full) < days or len(price2_full) < days:
                return None, None
            
            # 4. 提取最近N天数据(无需重复验证质量)
            recent_price1 = price1_full.tail(days)
            recent_price2 = price2_full.tail(days)
            
            return recent_price1, recent_price2
            
        except Exception as e:
            return None, None



    def PyMCModel(self, symbol1, symbol2, use_prior=False, data_range='full', prior_params=None):
        """
        统一的PyMC贝叶斯建模方法
        
        支持两种模式:
        1. 完整建模: use_prior=False, data_range='full' (默认)
        2. 动态更新: use_prior=True, data_range='recent'
        
        使用预验证的数据质量分类,简化数据获取和验证逻辑.
        
        Args:
            symbol1, symbol2: 协整对股票
            use_prior: 是否使用历史后验作为先验
            data_range: 数据范围 ('full': 全部数据, 'recent': 最近数据)
            prior_params: 先验参数字典, 当use_prior=True时必须提供
            
        Returns:
            dict: 后验参数字典, None表示建模失败
        """
        try:
            # 1. 基础数据质量门槛检查
            if (symbol1 not in self.data_quality['price_valid'] or 
                symbol2 not in self.data_quality['price_valid']):
                return None
            
            # 2. 准备建模数据(信任基础质量保证和_GetRecentData验证)
            if data_range == 'recent':
                # 动态更新:使用最近N天数据(_GetRecentData已验证质量)
                recent_price1, recent_price2 = self._GetRecentData(symbol1, symbol2, self.dynamic_update_lookback_days)
                if recent_price1 is None or recent_price2 is None:
                    return None
                    
                y_data = np.log(recent_price1.values)
                x_data = np.log(recent_price2.values)
            else:
                # 完整建模:直接使用预处理的缓存数据(quality已保证80%长度)
                price1 = self.historical_data_cache[symbol1]['close']
                price2 = self.historical_data_cache[symbol2]['close']
                
                y_data = np.log(price1.values)
                x_data = np.log(price2.values)
                
            # 3. 业务逻辑:最终建模数据合理性检查
            # 动态更新时放宽数据长度要求(30天),完整建模要求50天
            min_data_length = 30 if data_range == 'recent' else 50
            if len(y_data) < min_data_length:
                return None
            
            # 4. 配置先验分布参数
            if use_prior and prior_params is not None:
                # 验证先验参数
                if (len(prior_params.get('alpha_samples', [])) == 0 or 
                    len(prior_params.get('beta_samples', [])) == 0 or
                    len(prior_params.get('sigma_samples', [])) == 0):
                    return None
                
                # 使用历史后验作为先验
                alpha_mu = float(np.mean(prior_params['alpha_samples']))
                alpha_sigma = max(float(np.std(prior_params['alpha_samples'])), 0.1)
                beta_mu = float(np.mean(prior_params['beta_samples']))
                beta_sigma = max(float(np.std(prior_params['beta_samples'])), 0.1)
                
                # 改进的sigma先验设置:使用均值+2倍标准差作为HalfNormal的尺度参数
                sigma_mean = float(np.mean(prior_params['sigma_samples']))
                sigma_std = float(np.std(prior_params['sigma_samples']))
                sigma_sigma = max(sigma_mean + 2*sigma_std, 0.1)  # 覆盖约95%的历史分布
                
                # 调试日志
            else:
                # 使用无信息先验
                alpha_mu, alpha_sigma = 0, 10
                beta_mu, beta_sigma = 0, 10
                sigma_sigma = 1
            
            # 5. 构建PyMC模型
            with pm.Model() as model:
                # 先验分布
                alpha = pm.Normal('alpha', mu=alpha_mu, sigma=alpha_sigma)
                beta = pm.Normal('beta', mu=beta_mu, sigma=beta_sigma)
                sigmaOfEpsilon = pm.HalfNormal('sigmaOfEpsilon', sigma=sigma_sigma)
                
                # 线性关系
                mu = alpha + beta * x_data
                
                # 似然函数
                likelihood = pm.Normal('y', mu=mu, sigma=sigmaOfEpsilon, observed=y_data)
                
                # 残差计算
                residuals = pm.Deterministic('residuals', y_data - mu)
                
                # MCMC采样配置
                if data_range == 'recent':
                    # 动态更新:轻量级采样
                    draws = max(self.mcmc_draws // 2, 500)
                    tune = max(self.mcmc_burn_in // 2, 500)
                else:
                    # 完整建模:标准采样
                    draws = self.mcmc_draws
                    tune = self.mcmc_burn_in
                
                
                trace = pm.sample(draws=draws, tune=tune, 
                                chains=self.mcmc_chains, cores=1, progressbar=False)
                
                
                posterior = trace.posterior
                
                # 6. 提取后验参数
                posterior_params = {
                    'alpha': posterior['alpha'].values.flatten(),
                    'alpha_mean': posterior['alpha'].values.flatten().mean(),
                    'beta': posterior['beta'].values.flatten(),
                    'beta_mean': posterior['beta'].values.flatten().mean(),
                    'residuals': posterior['residuals'].values.flatten(),
                    'residuals_mean': posterior['residuals'].values.flatten().mean(),
                    'residuals_std': posterior['residuals'].values.flatten().std(),
                    'sigma_samples': posterior['sigmaOfEpsilon'].values.flatten()
                }
                
                # 7. 成功建模时总是保存历史后验(无论是完整建模还是动态更新)
                if posterior_params is not None:
                    pair_key = (symbol1, symbol2)
                    self.historical_posteriors[pair_key] = {
                        'alpha_samples': posterior_params['alpha'],
                        'beta_samples': posterior_params['beta'],
                        'sigma_samples': posterior_params['sigma_samples'],
                        'update_time': self.algorithm.Time
                    }
                    operation_type = "动态更新" if data_range == 'recent' else "完整建模"
                    
                    # 8. 建模成功，后续将在选股结束时统一记录到配对记账簿
                
                return posterior_params
                
        except ImportError as e:
            self.algorithm.Debug(f"[AlphaModel] PyMC导入错误: {str(e)[:50]}")
            return None
        except (ValueError, RuntimeError) as e:
            return None
        except Exception as e:
            operation_type = "动态更新" if data_range == 'recent' else "完整建模"
            self.algorithm.Debug(f"[AlphaModel] {operation_type}失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")
            return None
        


    def CalculateResidualZScore(self, symbol1, symbol2, data: Slice, posteriorParamSet):
        """
        计算当前价格相对于协整关系的偏离程度
        
        使用贝叶斯模型的后验参数计算当前时点的残差, 并标准化为z-score.
        z-score反映价格偏离协整均衡的程度, 是交易信号生成的核心指标.
        
        计算公式:
        residual = log(price1) - (alpha_mean + beta_mean × log(price2)) - residual_mean
        z_score = residual / residual_std
        
        Args:
            symbol1, symbol2: 协整对股票
            data: 当前市场数据切片
            posteriorParamSet: 贝叶斯模型后验参数
            
        Returns:
            dict: 更新了z-score的参数字典, None表示数据不可用
        """
        if not (data.ContainsKey(symbol1) and data[symbol1] is not None and data.ContainsKey(symbol2) and data[symbol2] is not None):
            return None
        
        current_price1 = np.log(data[symbol1].Close)
        current_price2 = np.log(data[symbol2].Close)
        
        alpha_mean = posteriorParamSet['alpha_mean']
        beta_mean = posteriorParamSet['beta_mean']  
        epsilon_mean = posteriorParamSet['residuals_mean']
        epsilon_std = posteriorParamSet['residuals_std']
        
        # 使用历史均值作基准调整, 避免z-score过大
        residual = current_price1 - (alpha_mean + beta_mean * current_price2) - epsilon_mean

        if epsilon_std != 0:   
            zscore = residual / epsilon_std
        else:
            zscore = 0
        posteriorParamSet['zscore'] = zscore

        return posteriorParamSet    



    def GenerateSignals(self, symbol1, symbol2, posteriorParamSet):
        """
        基于z-score生成配对交易信号
        
        根据价格偏离程度生成不同类型的交易信号:
        - 入场信号: z-score超过入场阈值时, 做空高估股票, 做多低估股票
        - 出场信号: z-score回归到退出阈值时, 平仓获利
        - 止损信号: z-score超过风险上限时, 强制平仓止损
        
        Args:
            symbol1, symbol2: 协整对股票
            posteriorParamSet: 包含z-score的参数字典
            
        Returns:
            list[Insight]: 配对交易信号列表, 包含两个相反方向的洞察
        """
        if posteriorParamSet is None:
            return []
        
        z = posteriorParamSet['zscore']
        
        # 根据z分数确定信号类型和方向
        signal_config = self._GetSignalConfiguration(z)
        if signal_config is None:
            # 观望阶段: 不发射任何信号
            return []
        
        insight1_direction, insight2_direction, trend = signal_config
        
        # 根据信号类型设置不同的有效期
        if insight1_direction == InsightDirection.Flat:
            # 平仓信号
            duration = timedelta(days=self.flat_signal_duration_days)
        else:
            # 建仓信号
            duration = timedelta(days=self.entry_signal_duration_days)
        
        # 直接生成信号,不做任何拦截或验证
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['alpha_mean']:.4f}|{posteriorParamSet['beta_mean']:.4f}|{z:.2f}|{len(self.industry_cointegrated_pairs)}"
        
        insight1 = Insight.Price(symbol1, duration, insight1_direction, tag=tag)
        insight2 = Insight.Price(symbol2, duration, insight2_direction, tag=tag)
        signals = [insight1, insight2]
        
        # No longer output any signal logs to reduce log volume
        return Insight.group(signals)
    

    
    def _GetSignalConfiguration(self, z):
        """
        将z-score映射到具体的交易信号配置
        
        信号区间划分:
        - [entry_threshold, upper_limit]: 做空symbol1, 做多symbol2
        - [lower_limit, -entry_threshold]: 做多symbol1, 做空symbol2  
        - (-exit_threshold, exit_threshold): 平仓回归信号
        - 超出upper/lower_limit: 止损平仓
        - 其他区间: 观望等待
        
        Args:
            z: 当前z-score值
            
        Returns:
            tuple: (direction1, direction2, trend_description) 或 None(观望)
        """
        if self.entry_threshold <= z <= self.upper_limit:
            return (InsightDirection.Down, InsightDirection.Up, "卖 | 买")
        elif self.lower_limit <= z <= -self.entry_threshold:
            return (InsightDirection.Up, InsightDirection.Down, "买 | 卖")
        elif -self.exit_threshold < z < self.exit_threshold:
            return (InsightDirection.Flat, InsightDirection.Flat, "回归")
        elif z > self.upper_limit or z < self.lower_limit:
            return (InsightDirection.Flat, InsightDirection.Flat, "失效")
        else:
            return None

    

    

