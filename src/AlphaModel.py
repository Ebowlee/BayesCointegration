# region imports
from AlgorithmImports import *
from datetime import timedelta
import itertools
from collections import defaultdict
from QuantConnect.Data.Fundamental import MorningstarSectorCode
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
        self.is_universe_selection_on = False
        self.symbols = []

        self.pvalue_threshold = 0.05                         # 协整检验的p值阈值
        self.correlation_threshold = 0.5                     # 协整检验的皮尔逊相关系数阈值
        self.max_symbol_repeats = 2                          # 每个股票在协整对中最多出现次数
        self.max_pairs = 2                                   # 最大协整对数量
        self.lookback_period = 252                           # 用于计算z分数的历史数据长度
        self.mcmc_burn_in = 1000                             # MCMC采样预热次数
        self.mcmc_draws = 1000                               # MCMC采样次数
        self.mcmc_chains = 1                                 # MCMC链数

        self.entry_threshold = 1.65                          # 入场阈值(标准差倍数)
        self.exit_threshold = 0.5                            # 出场阈值(标准差倍数)
        self.upper_limit = 3.0                               # 上限阈值(避免在极端情况下入场)
        self.lower_limit = -3.0                              # 下限阈值(避免在极端情况下入场)
        
        self.signal_duration = timedelta(days=30)

        self.algorithm.Debug("[AlphaModel] 初始化完成")



    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        Insights = []

        # 如果选股模块中没有返回股票或是股票数量小于2，则不生成任何信号
        if not self.symbols or len(self.symbols) < 2:
            self.algorithm.Debug("[AlphaModel] 当前未接受到足够数量的选股")
            return Insights
        
        # ==================================================周期和选股一致===========================================
        # 如果 OnSecuritiesChanged 被调用代表选股模块已经更新了股票池，则进行协整检验
        if self.is_universe_selection_on:
            self.algorithm.Debug(f"[AlphaModel] 选股日，接收到: {len(self.symbols)}")
            self.industry_cointegrated_pairs = {}
            self.posterior_params = {}                          

            # 获取同行业股票对
            sector_to_symbols = self.GetIntraIndustryPairs(self.symbols)

            # 配对同行业股票对
            for sector, symbols in sector_to_symbols.items():
                pairs = list(itertools.combinations(symbols, 2))
                intra_industry_pairs = {}

                # 遍历同行业股票对并作协整检验
                for pair_tuple in pairs:
                    symbol1, symbol2 = pair_tuple
                    cointegrated_pair = self.CointegrationTestForSinglePair(symbol1, symbol2, lookback_period=self.lookback_period)
                    intra_industry_pairs.update(cointegrated_pair)
                
                self.algorithm.Debug(f"[AlphaModel] 【{sector}】 生成协整对: {len(intra_industry_pairs):.0f}")
            
                # 过滤同行业协整对，使每个股票在同行业协整对中最多出现2次，最多保留2对
                filtered_intra_industry_pairs = self.FilterCointegratedPairs(intra_industry_pairs)
                # 将所有行业协整对汇总
                self.industry_cointegrated_pairs.update(filtered_intra_industry_pairs)

            self.algorithm.Debug(f"[AlphaModel] 筛选出协整对: [{', '.join([f'{symbol1.Value}-{symbol2.Value}' for symbol1, symbol2 in self.industry_cointegrated_pairs.keys()])}]")

            # 遍历协整对
            for pair_key in self.industry_cointegrated_pairs.keys():
                symbol1, symbol2 = pair_key

                # 使用 PyMC 模型计算后验参数
                posterior_param = self.PyMCModel(symbol1, symbol2, lookback_period=self.lookback_period)
                if posterior_param is not None:
                    self.posterior_params[pair_key] = posterior_param
                else:
                    self.algorithm.Debug(f"[AlphaModel] PyMC建模失败: {symbol1.Value}-{symbol2.Value}")
            
            # 协整检验、后验参数计算已经完成，设置标志位为False，等到下次选股在开启
            self.is_universe_selection_on = False

        # ==================================================周期为每日==================================================
        # 遍历后验参数，计算z-score并生成信号
        self.insight_blocked_count = 0  
        for pair_key in self.posterior_params.keys():
            symbol1, symbol2 = pair_key
            posterior_param = self.posterior_params[pair_key]
            posterior_param_with_zscore = self.CalculateResidualZScore(symbol1, symbol2, data, posterior_param)
            signal = self.GenerateSignals(symbol1, symbol2, posterior_param_with_zscore)
            Insights.extend(signal)
        if Insights:
            self.algorithm.Debug(f"[AlphaModel] 生成信号: {len(Insights)/2:.0f} 拦截重复信号: {self.insight_blocked_count:.0f}")
        return Insights    



    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        当宇宙选择模型选出的股票池发生变化时，由框架自动调用。
        用于更新 Alpha 模型内部跟踪的活跃股票列表和相关数据结构。
        """
        self.is_universe_selection_on = True

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
    
    

    def GetIntraIndustryPairs(self, symbols: list[Symbol]) -> dict:
        """
        获取同行业股票对
        """
        sector_map = {
                "Technology": MorningstarSectorCode.Technology,
                "Healthcare": MorningstarSectorCode.Healthcare,
                "Energy": MorningstarSectorCode.Energy,
                "ConsumerDefensive": MorningstarSectorCode.ConsumerDefensive,
                "CommunicationServices": MorningstarSectorCode.CommunicationServices,
                "Industrials": MorningstarSectorCode.Industrials,
                "Utilities": MorningstarSectorCode.Utilities
                }
        
        # 创建反向映射：从MorningstarSectorCode值到字符串key
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
        单个股票对协整检验
        """
        cointegrated_pair = {}
        is_cointegrated = False
        history1 = self.algorithm.History([symbol1], lookback_period, Resolution.Daily)
        history2 = self.algorithm.History([symbol2], lookback_period, Resolution.Daily)

        try:
            price1 = history1.loc[symbol1]['close'].fillna(method='pad')
            price2 = history2.loc[symbol2]['close'].fillna(method='pad')
            if len(price1) != len(price2) or price1.isnull().any() or price2.isnull().any():
                return cointegrated_pair
            
            correlation = price1.corr(price2)
            if abs(correlation) < self.correlation_threshold:
                return cointegrated_pair
            
            score, pvalue, critical_values = coint(price1, price2)
            is_cointegrated = pvalue < self.pvalue_threshold

        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 协整检验出错: {str(e)}")

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
        过滤协整对
        """
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = {}
        for keys, values in sorted_pairs:
            s1, s2 = keys
            if symbol_count[s1] <= self.max_symbol_repeats and symbol_count[s2] <= self.max_symbol_repeats:
                filtered_pairs[keys] = values
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) >= self.max_pairs:
                break
        return filtered_pairs
    


    def PyMCModel(self, symbol1, symbol2, lookback_period):
        """
        使用贝叶斯方法更新协整模型参数
        该函数利用PyMC3框架构建线性回归模型, 通过MCMC方法生成beta和alpha的联合后验分布,
        用于后续的残差计算和交易信号生成。
        """
        # 用两个资产在过去252天内的价格数据, 作为PyMC3模型的输入
        history1 = self.algorithm.History([symbol1], lookback_period, Resolution.Daily)
        history2 = self.algorithm.History([symbol2], lookback_period, Resolution.Daily)
        price1 = history1.loc[symbol1]['close'].fillna(method='pad')
        price2 = history2.loc[symbol2]['close'].fillna(method='pad')
        y = np.log(price1.values)
        x = np.log(price2.values)

        try:
            # 构建PyMC模型
            with pm.Model() as model:
                # 设置先验分布
                alpha = pm.Normal('alpha', mu=0, sigma=10)
                beta = pm.Normal('beta', mu=0, sigma=10)
                sigmaOfEpsilon = pm.HalfNormal('sigmaOfEpsilon', sigma=1)
                
                # 定义线性关系
                mu = alpha + beta * x
                
                # 定义似然函数 - 观测值y围绕预测值mu波动，波动程度由sigmaOfEpsilon控制
                likelihood = pm.Normal('y', mu=mu, sigma=sigmaOfEpsilon, observed=y)

                # 定义每个点的残差
                residuals = pm.Deterministic('residuals', y - mu)
                
                # 执行MCMC采样 - 前1000次预热，后1000次用于构建后验分布
                trace = pm.sample(draws=self.mcmc_draws, tune=self.mcmc_burn_in, chains=self.mcmc_chains, cores=1, progressbar=False)

                # 从后验分布中提取模型参数
                posterior = trace.posterior
                
                # 从后验分布中提取模型参数
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
            
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] PYMC 模型计算错误: {str(e)}")
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
        
        # 调取后验参数
        alpha_mean = posteriorParamSet['alpha_mean']
        beta_mean = posteriorParamSet['beta_mean']  
        epsilon_mean = posteriorParamSet['residuals_mean']
        epsilon_std = posteriorParamSet['residuals_std']
        
        # 计算 T+1 时点的残差与残差均值的偏离量（注意：残差的理论均值应该是 0，但实际中使用历史均值不然会造成 z 分数过大）
        residual = current_price1 - (alpha_mean + beta_mean * current_price2) - epsilon_mean

        # 计算z分数（标准化偏离量）
        if epsilon_std != 0:   
            zscore = residual / epsilon_std
        else:
            zscore = 0
        posteriorParamSet['zscore'] = zscore

        return posteriorParamSet    



    def GenerateSignals(self, symbol1, symbol2, posteriorParamSet):
        """
        根据z分数生成交易信号
        该函数根据计算出的z分数值, 基于预设阈值生成做多、做空或平仓信号。
        当价格偏离度超过阈值时生成反转交易信号，回归均值时生成平仓信号。
        """
        signals = []
        if posteriorParamSet is None:
            return signals
        
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['alpha_mean']:.4f}|{posteriorParamSet['beta_mean']:.4f}|{posteriorParamSet['zscore']:.2f}"
        z = posteriorParamSet['zscore']

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
            insight1 = Insight.Price(symbol1, self.signal_duration, insight1_direction, tag=tag)
            insight2 = Insight.Price(symbol2, self.signal_duration, insight2_direction, tag=tag)
            signals = [insight1, insight2]
            self.algorithm.Debug(f"[AlphaModel] : zscore {z:.4f}, 【{tag}】 [{symbol1.Value},{symbol2.Value}]")
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


    
    # def ShouldEmitInsightPair(self, symbol1, direction1, symbol2, direction2):
    #     # 判断当前两只股票信号是否已经存在
    #     filter_insights_1 = self.algorithm.insights.get_insights(lambda i: i.Symbol == symbol1 and i.Direction == direction1)
    #     filter_insights_2 = self.algorithm.insights.get_insights(lambda i: i.Symbol == symbol2 and i.Direction == direction2)

    #     # 如果两个股票都没有活跃信号，则可以生成信号
    #     if not filter_insights_1 and not filter_insights_2:
    #         return True
    #     else:
    #         return False