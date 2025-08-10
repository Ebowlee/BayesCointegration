# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, List
from datetime import timedelta
from .AlphaState import AlphaModelState
# endregion


# =============================================================================
# 信号生成模块 - SignalGenerator
# =============================================================================
class SignalGenerator:
    """
    信号生成器类 - 将统计模型转化为可执行的交易信号
    
    该类负责实时计算配对的偏离程度(z-score), 并根据阈值生成
    具体的交易方向。使用EMA平滑减少信号噪音, 提高稳定性。
    
    Z-Score计算原理:
    1. 计算当前价格关系的残差
    2. 标准化为z-score: (残差 - 均值) / 标准差
    3. EMA平滑: 80%当前值 + 20%历史值
    4. 解释: z>0表示股票1相对高估, z<0表示相对低估
    
    信号生成逻辑:
    - 建仓: |z-score| > entry_threshold (1.2)
      * z > 1.2: 做空股票1, 做多股票2
      * z < -1.2: 做多股票1, 做空股票2
    - 平仓: |z-score| < exit_threshold (0.3)
      * 价格关系回归均值, 平仓获利
    
    配置参数:
    - entry_threshold: 建仓阈值(默认1.2, 约88%置信区间外)
    - exit_threshold: 平仓阈值(默认0.3, 约23%置信区间内)
    - lower_limit: 极端偏离下限(默认-3.0)
    - flat_signal_duration_days: 平仓信号持续天数(默认1)
    - entry_signal_duration_days: 建仓信号持续天数(默认2)
    
    EMA平滑:
    - ema_alpha: 平滑系数(默认0.8)
    - 目的: 减少短期波动造成的频繁交易
    - 效果: 信号更稳定, 但反应稍有延迟
    
    Insight生成:
    - 使用Insight.Group确保配对同时执行
    - Tag包含关键参数便于追踪
    - Duration控制信号有效期
    
    注意事项:
    - 阈值选择需要平衡信号频率和质量
    - EMA平滑会造成信号延迟
    - 极端z-score可能预示模型失效
    """
    
    def __init__(self, algorithm, config: dict, state: AlphaModelState):
        """
        初始化信号生成器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含信号生成相关配置的字典
            state: AlphaModel的状态管理对象
        """
        self.algorithm = algorithm
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.upper_limit = config['upper_limit']  # 极端偏离上限
        self.lower_limit = config['lower_limit']
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 1)
        self.entry_signal_duration_days = config.get('entry_signal_duration_days', 2)
        self.state = state
        
        # EMA平滑系数：0.8表示80%权重给当前值，20%给历史值
        # 较高的alpha值使信号对新数据更敏感，较低则更平滑
        self.ema_alpha = 0.8
    
    def generate_signals(self, modeled_pairs: List[Dict], data) -> List:
        """
        为所有建模配对生成交易信号
        """
        insights = []
        
        for pair in modeled_pairs:
            pair_with_zscore = self._calculate_zscore(pair, data)
            if pair_with_zscore:
                pair_insights = self._generate_pair_signals(pair_with_zscore)
                # Insight.Group可能返回特殊对象，需要正确处理
                if pair_insights:
                    insights.extend(pair_insights)
                    
        return insights
    
    def _calculate_zscore(self, pair: Dict, data) -> Dict:
        """
        计算配对的当前z-score并应用EMA平滑
        
        Z-score衡量当前价格关系偏离历史均值的程度, 是触发交易的核心指标。
        使用EMA平滑可以减少短期噪音, 避免频繁交易。
        
        Args:
            pair: 配对信息字典，包含:
                - symbol1, symbol2: 股票代码
                - alpha_mean, beta_mean: 贝叶斯模型参数
                - residual_mean, residual_std: 残差统计量
            data: 当前市场数据(Slice对象)
            
        Returns:
            Dict: 更新后的配对信息，新增:
                - zscore: EMA平滑后的z-score
                - raw_zscore: 原始z-score(未平滑)
                - current_price1/2: 当前价格
            返回None如果数据不可用
            
        计算步骤:
            1. 获取当前价格并对数转换
            2. 使用模型参数计算期望值: E[log(P1)] = alpha + beta * log(P2)
            3. 计算残差: residual = log(P1) - E[log(P1)] - residual_mean
            4. 标准化: z-score = residual / residual_std
            5. EMA平滑: smoothed = 0.8 * current + 0.2 * previous
        
        交易含义:
            - z-score > 0: 股票1相对高估, 应做空1做多2
            - z-score < 0: 股票1相对低估, 应做多1做空2
            - |z-score| > 1.2: 偏离显著, 触发建仓
            - |z-score| < 0.3: 回归均值, 触发平仓
        """
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']
        
        # 验证并获取价格
        if not all([data.ContainsKey(s) and data[s] for s in [symbol1, symbol2]]):
            return None
        
        current_price1 = float(data[symbol1].Close)
        current_price2 = float(data[symbol2].Close)
        
        # 计算原始z-score
        log_price1 = np.log(current_price1)
        log_price2 = np.log(current_price2)
        
        # 基于贝叶斯模型计算期望价格关系
        expected = pair['alpha_mean'] + pair['beta_mean'] * log_price2
        # 计算去均值的残差
        residual = log_price1 - expected - pair['residual_mean']
        
        # 标准化得到z-score（避免除零）
        raw_zscore = residual / pair['residual_std'] if pair['residual_std'] > 0 else 0
        
        # EMA平滑处理
        pair_key = tuple(sorted([symbol1, symbol2]))
        zscore_ema = self.state.persistent['zscore_ema']
        
        if pair_key not in zscore_ema:
            # 首次计算，直接使用原始值
            smoothed_zscore = raw_zscore
        else:
            # 应用EMA平滑公式：EMA(t) = α * X(t) + (1-α) * EMA(t-1)
            # 这样可以减少短期噪音，避免频繁交易
            smoothed_zscore = self.ema_alpha * raw_zscore + (1 - self.ema_alpha) * zscore_ema[pair_key]
        
        # 更新EMA存储，供下次使用
        self.state.persistent['zscore_ema'][pair_key] = smoothed_zscore
        
        # 更新配对信息
        pair.update({
            'zscore': smoothed_zscore,  # 使用平滑后的值
            'raw_zscore': raw_zscore,   # 保留原始值供参考
            'current_price1': current_price1,
            'current_price2': current_price2
        })
        
        # 添加调试日志，监控z-score值
        # 只在生成信号时输出
        pass
        
        return pair
    
    def _create_insight_group(self, symbol1: Symbol, symbol2: Symbol, 
                             direction1: InsightDirection, direction2: InsightDirection,
                             duration_days: int, tag: str):
        """
        创建配对的Insight组
        
        注意: 返回Insight.Group()的原始结果, 不要用list()包装
        """
        return Insight.Group(
            Insight.Price(symbol1, timedelta(days=duration_days), direction1, 
                         None, None, None, None, tag),
            Insight.Price(symbol2, timedelta(days=duration_days), direction2,
                         None, None, None, None, tag)
        )
    
    def _generate_pair_signals(self, pair: Dict) -> List:
        """
        基于z-score为单个配对生成信号
        """
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']
        zscore = pair['zscore']
        
        # 构建标签 - 包含关键信息便于追踪和调试
        # 格式：symbol1&symbol2|alpha|beta|zscore|quality_score
        quality_score = pair.get('quality_score', 0.5)  # 默认0.5如果没有
        tag = f"{symbol1.Value}&{symbol2.Value}|{pair['alpha_mean']:.4f}|{pair['beta_mean']:.4f}|{zscore:.2f}|{quality_score:.3f}"
        
        # 风险检查 - 极端偏离
        if abs(zscore) > self.upper_limit:
            return self._create_insight_group(
                symbol1, symbol2, 
                InsightDirection.Flat, InsightDirection.Flat,
                self.flat_signal_duration_days, tag
            )
        
        # 建仓信号 - 价格偏离超过阈值
        if abs(zscore) > self.entry_threshold:
            # 持仓检查：两个资产都必须无持仓
            if not self.algorithm.Portfolio[symbol1].Invested and not self.algorithm.Portfolio[symbol2].Invested:
                # 根据z-score方向确定交易方向
                if zscore > 0:
                    # z>0: 股票1相对高估，做空1做多2
                    direction1, direction2 = InsightDirection.Down, InsightDirection.Up
                else:
                    # z<0: 股票1相对低估，做多1做空2
                    direction1, direction2 = InsightDirection.Up, InsightDirection.Down
                    
                return self._create_insight_group(
                    symbol1, symbol2, direction1, direction2,
                    self.entry_signal_duration_days, tag
                )
            # 如果有持仓，跳过建仓信号
            return []
        
        # 平仓信号 - 价格回归均值
        if abs(zscore) < self.exit_threshold:
            # 持仓检查：至少一个资产有持仓
            if self.algorithm.Portfolio[symbol1].Invested or self.algorithm.Portfolio[symbol2].Invested:
                return self._create_insight_group(
                    symbol1, symbol2,
                    InsightDirection.Flat, InsightDirection.Flat,
                    self.flat_signal_duration_days, tag
                )
            # 如果都没有持仓，跳过平仓信号
            return []
        
        return []