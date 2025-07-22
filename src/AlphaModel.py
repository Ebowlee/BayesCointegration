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
    
    def __init__(self, algorithm, config):
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
        """
        super().__init__() 
        self.algorithm = algorithm
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
        
        self.signal_duration = timedelta(days=30)
        
        self.historical_data_cache = {}

        self.algorithm.Debug(f"[AlphaModel] 初始化完成 (协整p值<{self.pvalue_threshold}, 最大{self.max_pairs}对, 波动率<{self.max_volatility_3month:.0%})")



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
            self.algorithm.Debug(f"[AlphaModel] 选股日, 接收到: {len(self.symbols)}")
            self.industry_cointegrated_pairs = {}
            self.posterior_params = {}
            
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
                    cointegrated_pair = self.CointegrationTestForSinglePair(symbol1, symbol2, lookback_period=self.lookback_period)
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
                detail_parts = []
                for sector, info in sector_pairs_info.items():
                    summary_parts.append(f"{sector}({info['count']})")
                    detail_parts.append(f"{sector}[{','.join(info['pairs'])}]")
                
                self.algorithm.Debug(f"[AlphaModel] 行业协整对: {' '.join(summary_parts)}")
                self.algorithm.Debug(f"[AlphaModel] 具体配对: {' '.join(detail_parts)}")
            
            self.algorithm.Debug(f"[AlphaModel] 筛选出协整对: {len(self.industry_cointegrated_pairs)}对")

            # 遍历协整对进行MCMC建模
            mcmc_success = 0
            mcmc_failed = 0
            
            # 对筛选后的协整对逐个进行贝叶斯建模
            # MCMC采样可能因数据质量或数值问题失败, 需要记录成功率
            for pair_key in self.industry_cointegrated_pairs.keys():
                symbol1, symbol2 = pair_key

                posterior_param = self.PyMCModel(symbol1, symbol2, lookback_period=self.lookback_period)
                if posterior_param is not None:
                    self.posterior_params[pair_key] = posterior_param
                    mcmc_success += 1
                else:
                    mcmc_failed += 1
            
            # 贝叶斯建模结果统计
            self.algorithm.Debug(f"[AlphaModel] 贝叶斯建模: 尝试{len(self.industry_cointegrated_pairs)}对 → 成功{mcmc_success}对 失败{mcmc_failed}对")
            
            self.is_universe_selection_on = False

        # 每日执行的信号生成逻辑
        self.insight_blocked_count = 0  
        self.insight_no_active_count = 0
        # 遍历所有成功建模的协整对, 计算当前价格偏离并生成交易信号
        for pair_key in self.posterior_params.keys():
            symbol1, symbol2 = pair_key
            posterior_param = self.posterior_params[pair_key]
            posterior_param_with_zscore = self.CalculateResidualZScore(symbol1, symbol2, data, posterior_param)
            signal = self.GenerateSignals(symbol1, symbol2, posterior_param_with_zscore)
            Insights.extend(signal)
        
        # 只在有意义的情况下输出日志
        signal_count = len(Insights) // 2
        if signal_count > 0 or self.insight_blocked_count > 0:
            self.algorithm.Debug(f"[AlphaModel] 生成信号: {signal_count}对 拦截: {self.insight_blocked_count} 观望: {self.insight_no_active_count}")
        
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
        批量加载历史数据到内存缓存
        
        使用单次History API调用获取所有股票数据, 相比逐个调用可显著提升性能.
        缓存数据用于后续的协整检验, 波动率计算和贝叶斯建模.
        
        Args:
            symbols: 需要获取历史数据的股票符号列表
            
        Note:
            缓存失败时会初始化为空字典, 后续操作会优雅降级
        """
        try:
            # 一次性获取所有股票的历史数据
            all_histories = self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
            
            # 将数据按symbol分别缓存
            self.historical_data_cache = {}
            for symbol in symbols:
                if symbol in all_histories.index.get_level_values(0):
                    symbol_data = all_histories.loc[symbol]
                    if not symbol_data.empty:
                        self.historical_data_cache[symbol] = symbol_data
                        
        except (KeyError, ValueError, IndexError) as e:
            self.algorithm.Debug(f"[AlphaModel] 数据处理错误: {str(e)[:50]}")
            self.historical_data_cache = {}
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 批量数据获取失败: {str(e)[:50]}")
            self.historical_data_cache = {}



    def _VolatilityFilter(self, symbols: list[Symbol]) -> list[Symbol]:
        """
        基于年化波动率进行股票筛选
        
        计算过去N天的年化波动率, 过滤掉高波动性股票以提高配对交易的稳定性.
        高波动股票的协整关系往往不稳定, 不适合均值回归策略.
        
        Args:
            symbols: 候选股票符号列表
            
        Returns:
            list[Symbol]: 通过波动率筛选的股票列表
            
        Note:
            年化波动率 = 日波动率 × sqrt(252), 阈值由配置参数控制
        """
        filtered_symbols = []
        volatility_failed = 0
        data_missing = 0
        
        for symbol in symbols:
            try:
                if symbol in self.historical_data_cache:
                    price_data = self.historical_data_cache[symbol]['close']
                    
                    recent_data = price_data.tail(self.volatility_lookback_days)
                    
                    # 只有数据充足时才计算波动率, 防止估计偏差
                    if len(recent_data) >= self.volatility_lookback_days:
                        returns = recent_data.pct_change().dropna()
                        
                        if len(returns) > 0:
                            daily_volatility = returns.std()
                            # 年化波动率 = 日波动率 × √252 (交易日年化)
                            annual_volatility = daily_volatility * np.sqrt(252)
                            
                            if annual_volatility <= self.max_volatility_3month:
                                filtered_symbols.append(symbol)
                            else:
                                volatility_failed += 1
                        else:
                            data_missing += 1
                    else:
                        data_missing += 1
                else:
                    data_missing += 1
                    
            except (KeyError, ValueError, ZeroDivisionError) as e:
                self.algorithm.Debug(f"[AlphaModel] 波动率计算错误 {symbol.Value}: {str(e)[:30]}")
                data_missing += 1
            except Exception as e:
                self.algorithm.Debug(f"[AlphaModel] 波动率计算异常 {symbol.Value}: {str(e)[:30]}")
                data_missing += 1
        
        # 日志输出
        total_input = len(symbols)
        total_filtered = len(filtered_symbols)
        total_failed = volatility_failed + data_missing
        
        # 合并输出为一行，包含明细
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



    def CointegrationTestForSinglePair(self, symbol1, symbol2, lookback_period):
        """
        对单个股票对执行Engle-Granger协整检验
        
        协整检验分为两步: 1) 相关性预筛选 2) Engle-Granger统计检验
        只有通过两步检验的股票对才被认为存在长期均衡关系.
        
        Args:
            symbol1, symbol2: 待检验的股票对
            lookback_period: 历史数据回看期长度
            
        Returns:
            dict: 协整对信息字典, 空字典表示未通过检验
                 格式: {(symbol1, symbol2): {"pvalue": 0.01, "critical_values": [...]}}
        """
        cointegrated_pair = {}
        is_cointegrated = False

        try:
            # 从缓存中获取数据
            if symbol1 not in self.historical_data_cache or symbol2 not in self.historical_data_cache:
                return cointegrated_pair
                
            price1 = self.historical_data_cache[symbol1]['close'].fillna(method='pad')
            price2 = self.historical_data_cache[symbol2]['close'].fillna(method='pad')
            if len(price1) != len(price2) or price1.isnull().any() or price2.isnull().any():
                return cointegrated_pair
            
            # 预筛选: 相关性过低的股票对不太可能存在协整关系
            correlation = price1.corr(price2)
            if abs(correlation) < self.correlation_threshold:
                return cointegrated_pair
            
            # Engle-Granger协整检验: 检验价格序列的线性组合是否平稳
            score, pvalue, critical_values = coint(price1, price2)
            is_cointegrated = pvalue < self.pvalue_threshold

        except (KeyError, ValueError) as e:
            self.algorithm.Debug(f"[AlphaModel] 数据错误: {str(e)[:50]}")
        except ImportError as e:
            self.algorithm.Debug(f"[AlphaModel] 统计库错误: {str(e)[:50]}")
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 协整检验异常: {str(e)[:50]}")

        if is_cointegrated:
            pair_key = (symbol1, symbol2)
            cointegrated_pair[pair_key] = {
                'pvalue': pvalue,
                'critical_values': critical_values,
                'create_time': self.algorithm.Time
            }
        return cointegrated_pair
    


    def FilterCointegratedPairs(self, cointegrated_pairs) -> dict:
        """
        按质量筛选协整对, 控制投资组合规模
        
        根据p值排序选择最优协整对, 同时限制每只股票的重复使用次数.
        避免过度集中和降低整体组合风险.
        
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
    


    def PyMCModel(self, symbol1, symbol2, lookback_period):
        """
        使用PyMC进行贝叶斯线性回归建模
        
        构建线性回归模型: log(price1) = alpha + beta × log(price2) + epsilon
        通过MCMC采样获得alpha, beta和残差的完整后验分布, 相比OLS回归能更好地量化参数不确定性.
        
        建模流程:
        1. 设置先验分布: alpha~N(0,10), beta~N(0,10), sigma~HalfNormal(1)
        2. 定义似然函数: 观测价格围绕线性预测值的正态分布
        3. MCMC采样: 获取参数后验样本
        4. 提取统计量: 均值, 标准差用于后续z-score计算
        
        Args:
            symbol1, symbol2: 协整对股票
            lookback_period: 回看期长度
            
        Returns:
            dict: 后验参数字典, 包含alpha, beta均值和残差统计量
                 None表示建模失败
        """
        try:
            # 从缓存中获取数据
            if symbol1 not in self.historical_data_cache or symbol2 not in self.historical_data_cache:
                return None
                
            price1 = self.historical_data_cache[symbol1]['close'].fillna(method='pad')
            price2 = self.historical_data_cache[symbol2]['close'].fillna(method='pad')
            # 使用对数价格进行建模, 减少价格水平和波动率影响
            y = np.log(price1.values)
            x = np.log(price2.values)

            # 构建PyMC模型
            with pm.Model() as model:
                # 设置先验分布
                alpha = pm.Normal('alpha', mu=0, sigma=10)
                beta = pm.Normal('beta', mu=0, sigma=10)
                sigmaOfEpsilon = pm.HalfNormal('sigmaOfEpsilon', sigma=1)
                
                # 定义线性关系
                mu = alpha + beta * x
                
                likelihood = pm.Normal('y', mu=mu, sigma=sigmaOfEpsilon, observed=y)

                residuals = pm.Deterministic('residuals', y - mu)
                
                trace = pm.sample(draws=self.mcmc_draws, tune=self.mcmc_burn_in, chains=self.mcmc_chains, cores=1, progressbar=False)

                posterior = trace.posterior
                
                posteriorParamSet = {}
                posteriorParamSet= {
                    'alpha': posterior['alpha'].values.flatten(),                              # 数据维度是(chains, draws)
                    'alpha_mean': posterior['alpha'].values.flatten().mean(),
                    'beta': posterior['beta'].values.flatten(),                                # 数据维度是(chains, draws)
                    'beta_mean': posterior['beta'].values.flatten().mean(),
                    'residuals': posterior['residuals'].values.flatten(),                      # 数据维度是(chains, draws, lookback_period)
                    'residuals_mean': posterior['residuals'].values.flatten().mean(),
                    'residuals_std': posterior['residuals'].values.flatten().std(), 
                }
            return posteriorParamSet
            
        except ImportError as e:
            self.algorithm.Debug(f"[AlphaModel] PyMC导入错误: {str(e)[:50]}")
            return None
        except (ValueError, RuntimeError) as e:
            self.algorithm.Debug(f"[AlphaModel] MCMC采样错误: {str(e)[:50]}")
            return None
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 贝叶斯建模异常: {str(e)[:50]}")
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
            self.insight_no_active_count += 1
            return []
        
        insight1_direction, insight2_direction, trend = signal_config
        
        # 检查是否应该发射信号
        if not self.ShouldEmitInsightPair(symbol1, insight1_direction, symbol2, insight2_direction):
            self.insight_blocked_count += 1
            return []
        
        # 生成信号标签和洞察
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['alpha_mean']:.4f}|{posteriorParamSet['beta_mean']:.4f}|{z:.2f}|{len(self.industry_cointegrated_pairs)}"
        
        insight1 = Insight.Price(symbol1, self.signal_duration, insight1_direction, tag=tag)
        insight2 = Insight.Price(symbol2, self.signal_duration, insight2_direction, tag=tag)
        signals = [insight1, insight2]
        
        self.algorithm.Debug(f"[AlphaModel] zscore {z:.4f} [{trend}] {symbol1.Value}-{symbol2.Value}")
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

    

    def ShouldEmitInsightPair(self, symbol1, direction1, symbol2, direction2):
        """
        检查是否应该发出交易信号, 避免重复和无效信号
        
        信号过滤规则:
        1. 避免无意义的平仓信号: 当没有持仓时不发出Flat信号
        2. 避免重复信号: 检查是否已存在相同方向的活跃信号
        
        Args:
            symbol1, symbol2: 交易对股票
            direction1, direction2: 对应的交易方向
            
        Returns:
            bool: True表示应该发出信号, False表示应该拦截
        """
        # 获取两个symbol的活跃洞察
        active_insights_1 = self._GetActiveInsights(symbol1)
        active_insights_2 = self._GetActiveInsights(symbol2)
        
        # 检查是否要发射无意义的Flat信号
        if self._IsMeaninglessFlatSignal(direction1, direction2, active_insights_1, active_insights_2):
            return False
        
        # 检查是否存在重复信号
        if self._HasDuplicateSignal(active_insights_1, active_insights_2, direction1, direction2):
            return False
        
        return True
    
    def _GetActiveInsights(self, symbol):
        """获取指定股票的当前活跃交易信号"""
        return [ins for ins in self.algorithm.insights 
                if ins.Symbol == symbol and ins.IsActive(self.algorithm.utc_time)]
    
    def _IsMeaninglessFlatSignal(self, direction1, direction2, active_insights_1, active_insights_2):
        """检查是否为无意义的Flat信号"""
        return (direction1 == InsightDirection.Flat and direction2 == InsightDirection.Flat and
                not active_insights_1 and not active_insights_2)
    
    def _HasDuplicateSignal(self, active_insights_1, active_insights_2, direction1, direction2):
        """检查是否存在重复信号"""
        for ins1 in active_insights_1:
            for ins2 in active_insights_2:
                if (ins1.GroupId == ins2.GroupId and 
                    (ins1.Direction, ins2.Direction) == (direction1, direction2)):
                    return True
        return False