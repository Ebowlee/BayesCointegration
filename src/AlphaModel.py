# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from scipy import stats
import pymc as pm
from datetime import timedelta
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
        
        参数:
            algorithm:  QCAlgorithm实例, 用于访问数据和记录日志
        """
        self.algorithm = algorithm
        self.lookback_period = 252  # 用于计算z分数的历史数据长度

        # 交易信号阈值
        self.entry_threshold = 1.65  # 入场阈值(标准差倍数)
        self.exit_threshold = 0.5   # 出场阈值(标准差倍数)
        self.upper_bound = 3.0      # 上限阈值(避免在极端情况下入场)
        self.lower_bound = -3.0     # 下限阈值(避免在极端情况下入场)
        
        # 信号持续时间(以天为单位)
        self.signal_duration = timedelta(days=15)

        self.algorithm.Debug("[AlphaModel] 初始化完成")



    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        Insights = []

        # 如果universeSelectionModel中没有协整对，则不生成任何信号
        if not hasattr(self.algorithm.universeSelectionModel, 'cointegrated_pairs') or not self.algorithm.universeSelectionModel.cointegrated_pairs:
            self.algorithm.Debug("[AlphaModel] -- [Update] 接收不到协整对")
            return Insights

        self.algorithm.Debug(f"[AlphaModel] -- [Update] 接收到协整对数量: {len(self.algorithm.universeSelectionModel.cointegrated_pairs)}")

        # 遍历协整对
        for pair_key, _ in self.algorithm.universeSelectionModel.cointegrated_pairs.items():
            symbol1, symbol2 = pair_key

            # 获取历史数据
            history1 = algorithm.History([symbol1], self.lookback_period, Resolution.Daily)
            history2 = algorithm.History([symbol2], self.lookback_period, Resolution.Daily)

            if history1.empty or history2.empty:
                self.algorithm.Debug(f"[AlphaModel] -- [Update] 协整对 {symbol1.Value} or {symbol2.Value} 历史数据缺失")
                continue

            try:
                price1 = history1.loc[symbol1]['close']
                price2 = history2.loc[symbol2]['close']
            except Exception as e:
                self.algorithm.Debug(f"[AlphaModel] -- [Update] 协整对 {symbol1.Value} or {symbol2.Value} 提取价格失败: {str(e)}")
                continue

            # 获取联合后验参数
            posterior_params = self.PyMCModel(price1, price2)
            if posterior_params is None:
                self.algorithm.Debug(f"[AlphaModel] -- [Update] 协整对 {symbol1.Value} or {symbol2.Value} PYMC 建模失败")
                continue

            # 计算z-score并生成信号
            posterior_params_and_zscore = self.CalculateResidualZScore(price1, price2, posterior_params)
            signal = self.GenerateSignals(pair_key, posterior_params_and_zscore)
            Insights.extend(signal)
        
        if Insights:
            self.algorithm.Debug(f"[AlphaModel] -- [Update] 本轮生成信号: {len(Insights)}")
        else:
            self.algorithm.Debug("[AlphaModel] -- [Update] 本轮未生成信号")

        return Insights



    def PyMCModel(self, price1, price2):
        """
        使用贝叶斯方法更新协整模型参数
        
        该函数利用PyMC3框架构建线性回归模型, 通过MCMC方法生成beta和alpha的联合后验分布,
        用于后续的残差计算和交易信号生成。
        
        参数:
            price1:  第一只股票的价格序列
            price2:  第二只股票的价格序列
            
        返回:
            dict: 包含所有参数集的后验分布列表, 每组包含alpha、beta和sigma
        """
        # 用两个资产在过去252天内的价格数据, 作为PyMC3模型的输入
        # y = price1.values
        # x = price2.values

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
                trace = pm.sample(1000, tune=1000, chains=1, cores=1, progressbar=False)

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
        


    def CalculateResidualZScore(self, price1, price2, posteriorParamSet):
        """
        计算残差并标准化为z分数
        
        该函数利用贝叶斯参数后验分布，计算当前价格的残差分布，
        并将其标准化为z分数, 用于判断价格偏离程度和生成交易信号。
        
        参数:
            price1:  第一只股票的价格序列
            price2:  第二只股票的价格序列
            posteriorParamSet:  包含多组模型参数的列表
            
        返回:
            float: 标准化后的z分数值
        """
        # 获取当前价格（最后一个时间点）
        # current_price1 = price1.iloc[-1]
        # current_price2 = price2.iloc[-1]
        current_price1 = np.log(price1.iloc[-1])
        current_price2 = np.log(price2.iloc[-1])
        
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
        
        参数:
            pair_id:  股票对标识符 (symbol1, symbol2)
            posteriorParamSet:  包含多组模型参数的列表
            
        返回:
            list[Insight]: 包含做多/做空/平仓信号的Insight对象列表 (可能为空)
        """
        signals = []
        symbol1, symbol2 = pair_id
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['beta_mean']:.4f}|{posteriorParamSet['zscore']:.2f}|{posteriorParamSet['confidence_interval']}"
        z = posteriorParamSet['zscore']

        if self.entry_threshold < z < self.upper_bound:
            if self.ShouldEmitInsightPair(symbol1, InsightDirection.Down, symbol2, InsightDirection.Up):
                insight1 = Insight.Price(symbol1, self.signal_duration, InsightDirection.Down, tag=tag)
                insight2 = Insight.Price(symbol2, self.signal_duration, InsightDirection.Up, tag=tag)
                self.algorithm.Debug(f"[AlphaModel] -- [GenerateSignals]: zscore {z:.4f}, 做空 {symbol1.Value}, 做多 {symbol2.Value}")
                signals = [insight1, insight2]

        elif self.lower_bound < z < -self.entry_threshold:
            if self.ShouldEmitInsightPair(symbol1, InsightDirection.Up, symbol2, InsightDirection.Down):
                insight1 = Insight.Price(symbol1, self.signal_duration, InsightDirection.Up, tag=tag)
                insight2 = Insight.Price(symbol2, self.signal_duration, InsightDirection.Down, tag=tag)
                self.algorithm.Debug(f"[AlphaModel] -- [GenerateSignals]: zscore {z:.4f}, 做多 {symbol1.Value}, 做空 {symbol2.Value}")
                signals = [insight1, insight2]

        elif -self.exit_threshold <= z <= self.exit_threshold:
            if self.HasActiveInsight(symbol1) or self.HasActiveInsight(symbol2):
                insight1 = Insight.Price(symbol1, self.signal_duration, InsightDirection.Flat, tag=tag)
                insight2 = Insight.Price(symbol2, self.signal_duration, InsightDirection.Flat, tag=tag)
                self.algorithm.Debug(f"[AlphaModel] -- [GenerateSignals]: zscore {z:.4f}, 回归平仓 {symbol1.Value}, 回归平仓 {symbol2.Value}")
                signals = [insight1, insight2]

        elif z >= self.upper_bound or z <= self.lower_bound:
            if self.HasActiveInsight(symbol1) or self.HasActiveInsight(symbol2):
                insight1 = Insight.Price(symbol1, self.signal_duration, InsightDirection.Flat, tag=tag)
                insight2 = Insight.Price(symbol2, self.signal_duration, InsightDirection.Flat, tag=tag)
                self.algorithm.Debug(f"[AlphaModel] -- [GenerateSignals]: zscore {z:.4f}, 越界平仓 {symbol1.Value}, 越界平仓 {symbol2.Value}")
                signals = [insight1, insight2]

        return Insight.group(signals)
    


    def HasActiveInsight(self, symbol):
        return self.algorithm.insights.has_active_insights(symbol, self.algorithm.utc_time)



    def ShouldEmitInsightPair(self, symbol1, direction1, symbol2, direction2):
        active_insights = self.algorithm.insights.get_active_insights(self.algorithm.utc_time)
        for insight in active_insights:
            if (insight.Symbol == symbol1 and insight.Direction == direction1) or \
            (insight.Symbol == symbol2 and insight.Direction == direction2):
                return False
        return True



    

