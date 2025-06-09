# region imports
from AlgorithmImports import *
from datetime import timedelta
import itertools
from collections import defaultdict
from statsmodels.tsa.stattools import coint
import numpy as np
import pymc as pm
import scipy.stats as stats
# endregion

class BayesianCointegrationAlphaModel(AlphaModel):
    """
    贝叶斯协整Alpha模型
    
    该模型通过基于贝叶斯方法并通过MCMC方法来生成 beta 和 alpha 的联合后验分布
    通过beta alpha 的联合后验分布, 计算残差分布, 并计算残差z-score(理论依据是协整对残差平稳均值为零)
    通过持续监控价格偏离程度和均值回归特性, 在合适时机产生做多/做空信号。
    """
    
    def __init__(self, algorithm):
        """
        初始化Alpha模型
        """
        super().__init__() 
        self.algorithm = algorithm
        self.symbols = []
        self.last_cointegration_date = None
        self.cointegration_interval = timedelta(days=30)
        self.cointegrated_pairs = {}
        self.price = {}
        self.pvalue_threshold = 0.05                         # 协整检验的p值阈值
        self.correlation_threshold = 0.5                     # 协整检验的皮尔逊相关系数阈值
        self.max_symbol_repeats = 2                          # 每个股票在协整对中最多出现次数
        self.max_pairs = 10                                  # 最大协整对数量
        self.lookback_period = 252                           # 用于计算z分数的历史数据长度
        self.mcmc_burn_in = 1000                             # MCMC采样预热次数
        self.mcmc_draws = 1000                               # MCMC采样次数
        self.mcmc_chains = 2                                 # MCMC链数
        self.posterior_params_cache = {}                     # 缓存后验参数

        self.entry_threshold = 1.65                          # 入场阈值(标准差倍数)
        self.exit_threshold = 0.5                            # 出场阈值(标准差倍数)
        self.upper_limit = 3.0                               # 上限阈值(避免在极端情况下入场)
        self.lower_limit = -3.0                              # 下限阈值(避免在极端情况下入场)
        
        self.signal_duration = timedelta(days=15)
        self.insight_blocked_count = 0

        self.algorithm.Debug("[AlphaModel] 初始化完成")
    


    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        当宇宙选择模型选出的股票池发生变化时，由框架自动调用。
        用于更新 Alpha 模型内部跟踪的活跃股票列表和相关数据结构。
        """
        # 处理新增的证券
        for added_security in changes.AddedSecurities:
            symbol = added_security.Symbol
            if symbol and symbol not in self.symbols:
                self.symbols.append(symbol)
    
        # 处理移除的证券
        for removed_security in changes.RemovedSecurities:
            symbol = removed_security.Symbol
            if symbol and symbol in self.symbols:
                self.symbols.remove(symbol)

        if not self.symbols:
            self.algorithm.Log("[AlphaModel] -- [OnSecuritiesChanged] Alpha模型当前没有活跃股票。")
        else:
            self.algorithm.Debug(f"[AlphaModel] -- [OnSecuritiesChanged] Alpha模型当前活跃股票数量: {len(self.symbols)}")



    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        Insights = []

        # 如果universeSelectionModel中没有协整对，则不生成任何信号
        if not self.symbols or len(self.symbols) < 2:
            self.algorithm.Debug("[AlphaModel] -- [Update] 当前没有足够的活跃股票")
            return Insights
        
        current_date = self.algorithm.Time.date()

        # 如果上次协整检验的时间距离现在超过10天，则进行协整检验
        if self.last_cointegration_date is None or (current_date - self.last_cointegration_date).days >= self.cointegration_interval.days:
            self.cointegrated_pairs = {}
            self.price = {}
            pairs = list(itertools.combinations(self.symbols, 2))

            for pair_tuple in pairs:
                symbol1, symbol2 = pair_tuple
                cointegrated_pair, price = self.CointegrationTestForSinglePair(symbol1, symbol2)
                self.cointegrated_pairs.update(cointegrated_pair)
                self.price.update(price)
            
            self.last_cointegration_date = current_date
                
            # 过滤协整对，使每个股票在协整对中最多出现两次
            self.cointegrated_pairs = self.FilterCointegratedPairs(self.cointegrated_pairs)
            self.algorithm.Debug(f"[AlphaModel] -- [Update] 本轮协整对数量: {len(self.cointegrated_pairs)}")
            self.algorithm.Debug(f"[AlphaModel] -- [Update] 本轮协整对: [{', '.join([f'{symbol1.Value}-{symbol2.Value}' for symbol1, symbol2 in self.cointegrated_pairs.keys()])}]")

            # 遍历协整对
            for pair_key in self.cointegrated_pairs.keys():
                symbol1, symbol2 = pair_key

                # 获取存储在self.price中的历史数据
                price1 = self.price[symbol1]
                price2 = self.price[symbol2]

                # 使用 PyMC 模型计算后验参数
                posterior_params = self.PyMCModel(price1, price2)
                if posterior_params is not None:
                    self.posterior_params_cache[pair_key] = posterior_params  # 缓存参数
                else:
                    self.algorithm.Debug(f"[AlphaModel] -- [Update] PyMC建模失败: {symbol1.Value}-{symbol2.Value}")

        for pair_key in self.cointegrated_pairs.keys():
            if pair_key in self.posterior_params_cache:
                symbol1, symbol2 = pair_key
                cached_params = self.posterior_params_cache[pair_key]
            else:
                self.algorithm.Debug(f"[AlphaModel] -- [Update] 缓存中没有找到后验参数: {symbol1.Value}-{symbol2.Value}")
                continue
        
            # 利用后验参数，刻画后验分布，计算z-score并生成信号
            posterior_params_and_zscore = self.CalculateResidualZScore(symbol1, symbol2, data, cached_params)
            signal = self.GenerateSignals(pair_key, posterior_params_and_zscore)
            Insights.extend(signal)
        
        self.algorithm.Debug(f"[AlphaModel] -- [Update] 本轮拦截重复信号: {self.insight_blocked_count}")

        if Insights:
            self.algorithm.Debug(f"[AlphaModel] -- [Update] 本轮生成信号: {len(Insights)/2:.0f}")
        else:
            self.algorithm.Debug("[AlphaModel] -- [Update] 本生成信号：{[0]}")

        return Insights    



    def CointegrationTestForSinglePair(self, symbol1, symbol2):
        """
        单个股票对协整检验
        """
        cointegrated_pair = {}
        is_cointegrated = False
        price = {}

        historySymbol1 = self.algorithm.History([symbol1], self.lookback_period, Resolution.Daily)
        historySymbol2 = self.algorithm.History([symbol2], self.lookback_period, Resolution.Daily)

        try:
            price[symbol1] = historySymbol1.loc[symbol1]['close'].fillna(method='pad')
            price[symbol2] = historySymbol2.loc[symbol2]['close'].fillna(method='pad')
            if len(price[symbol1]) != len(price[symbol2]) or price[symbol1].isnull().any() or price[symbol2].isnull().any():
                return cointegrated_pair, price
            
            correlation = price[symbol1].corr(price[symbol2])
            if abs(correlation) < self.correlation_threshold:
                return cointegrated_pair, price
            score, pvalue, critical_values = coint(price[symbol1], price[symbol2])
            is_cointegrated = pvalue < self.pvalue_threshold

        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForSinglePair] 协整检验出错: {str(e)}")

        if is_cointegrated:
            pair_key = (symbol1, symbol2)
            cointegrated_pair[pair_key] = {
                'pvalue': pvalue,
                'critical_values': critical_values,
                'create_time': self.algorithm.Time
            }
            self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForPairs] 发现协整对: [{symbol1.Value} - {symbol2.Value}], p值: {pvalue:.4f}")
        return cointegrated_pair, price
    


    def FilterCointegratedPairs(self, cointegrated_pairs) -> dict:
        """
        过滤协整对
        """
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = {}
        for keys, values in sorted_pairs:
            s1, s2 = keys
            if symbol_count[s1] < self.max_symbol_repeats and symbol_count[s2] < self.max_symbol_repeats:
                filtered_pairs[keys] = values
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) > self.max_pairs:
                break
        return filtered_pairs
    


    def PyMCModel(self, price1, price2):
        """
        使用贝叶斯方法更新协整模型参数

        该函数利用PyMC3框架构建线性回归模型, 通过MCMC方法生成beta和alpha的联合后验分布,
        用于后续的残差计算和交易信号生成。
        """
        # 用两个资产在过去252天内的价格数据, 作为PyMC3模型的输入
        y = np.log(price1.values)
        x = np.log(price2.values)

        try:
            # 构建PyMC3模型
            with pm.Model() as model:
                # 设置先验分布
                alpha = pm.Normal('alpha', mu=0, sigma=10)
                beta = pm.Normal('beta', mu=0, sigma=10)
                sigmaOfEpsilon = pm.HalfNormal('sigmaOfEpsilon', sigma=1)
                
                # 定义线性关系
                mu = alpha + beta * x
                
                # 定义似然函数 - 观测值y围绕预测值mu波动，波动程度由sigmaOfEpsilon控制
                likelihood = pm.Normal('y', mu=mu, sigma=sigmaOfEpsilon, observed=y)
                
                # 执行MCMC采样 - 前1000次预热，后1000次用于构建后验分布
                trace = pm.sample(draws=self.mcmc_draws, tune=self.mcmc_burn_in, chains=self.mcmc_chains, cores=1, progressbar=False)

                # 从后验分布中提取模型参数
                posterior = trace.posterior
                
                # 从后验分布中提取模型参数
                posteriorParamSet = {}
                posteriorParamSet= {
                    'alpha': posterior['alpha'].values.flatten(),
                    'beta': posterior['beta'].values.flatten(),
                    'beta_mean': posterior['beta'].values.flatten().mean(),
                    'sigmaOfEpsilon': posterior['sigmaOfEpsilon'].values.flatten()   
                }
            
            return posteriorParamSet
            
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] -- [PyMCModel] PYMC 模型计算错误: {str(e)}")
            return None
        


    def CalculateResidualZScore(self, symbol1, symbol2, data: Slice, posteriorParamSet):
        """
        计算残差并标准化为z分数

        该函数利用贝叶斯参数后验分布，计算当前价格的残差分布，
        并将其标准化为z分数, 用于判断价格偏离程度和生成交易信号。
        """
        if not (data.ContainsKey(symbol1) and data[symbol1] is not None and data.ContainsKey(symbol2) and data[symbol2] is not None):
            return None
        
        current_price1 = np.log(data[symbol1].Close)
        current_price2 = np.log(data[symbol2].Close)
        
        # 利用所有后验参数计算残差分布
        alpha = posteriorParamSet['alpha']
        beta = posteriorParamSet['beta']  
        
        # 计算预期价格和残差
        expected_price1 = alpha + beta * current_price2
        residual = current_price1 - expected_price1
        
        # 计算残差的均值和标准差
        residual_mean = np.mean(residual)
        residual_std = np.std(residual)
        
        # 计算z分数（标准化残差）
        if residual_std != 0:   
            zscore = residual_mean / residual_std
        else:
            zscore = 0
        
        # 计算残差分布的置信区间
        confidence_interval = stats.norm.interval(0.95, loc=residual_mean, scale=residual_std)

        posteriorParamSet['zscore'] = zscore
        posteriorParamSet['confidence_interval'] = confidence_interval
        
        return posteriorParamSet    



    def GenerateSignals(self, pair_id, posteriorParamSet):
        """
        根据z分数生成交易信号
        
        该函数根据计算出的z分数值, 基于预设阈值生成做多、做空或平仓信号。
        当价格偏离度超过阈值时生成反转交易信号，回归均值时生成平仓信号。
        """
        signals = []

        if posteriorParamSet is None:
            return signals
        
        symbol1, symbol2 = pair_id
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['beta_mean']:.4f}|{posteriorParamSet['zscore']:.2f}|{posteriorParamSet['confidence_interval']}"
        z = posteriorParamSet['zscore']
        tag = None

        if self.entry_threshold <= z <= self.upper_limit:
            insight1_direction = InsightDirection.Down
            insight2_direction = InsightDirection.Up
            tag = "跌 | 涨"
        elif self.lower_limit <= z <= -self.entry_threshold:
            insight1_direction = InsightDirection.Up
            insight2_direction = InsightDirection.Down
            tag = "涨 | 跌"
        elif -self.exit_threshold < z < self.exit_threshold:
            insight1_direction = InsightDirection.Flat
            insight2_direction = InsightDirection.Flat  
            tag = "回归"
        elif z > self.upper_limit or z < self.lower_limit:
            insight1_direction = InsightDirection.Flat
            insight2_direction = InsightDirection.Flat
            tag = "失效"
        else:
            insight1_direction = InsightDirection.Flat
            insight2_direction = InsightDirection.Flat  
            tag = "观望"
        
        if self.ShouldEmitInsightPair(symbol1, insight1_direction, symbol2, insight2_direction):
            insight1 = Insight.Price(symbol1, self.signal_duration, insight1_direction)
            insight2 = Insight.Price(symbol2, self.signal_duration, insight2_direction)
            signals = [insight1, insight2]
            self.algorithm.Debug(f"[AlphaModel] -- [GenerateSignals]: zscore {z:.4f}, 【{tag}】 [{symbol1.Value},{symbol2.Value}]")
        else:
            self.insight_blocked_count += 1

        return Insight.group(signals)
    


    def ShouldEmitInsightPair(self, symbol1, direction1, symbol2, direction2):
        # 从 Insight Manager 获取当前时间这两个 symbol 的所有活跃 Insight
        active_insights_1 = [ins for ins in self.algorithm.insights if ins.symbol == symbol1 and ins.is_active(self.algorithm.utc_time)]
        active_insights_2 = [ins for ins in self.algorithm.insights if ins.symbol == symbol2 and ins.is_active(self.algorithm.utc_time)]

        if not active_insights_1 or not active_insights_2:
            return True

        # 遍历两组 insight，查找是否存在一组 GroupId 相同 + 方向相同
        for ins1 in active_insights_1:
            for ins2 in active_insights_2:
                if ins1.GroupId == ins2.GroupId:
                    if (ins1.Direction, ins2.Direction) == (direction1, direction2):
                        return False
        return True 





   
        