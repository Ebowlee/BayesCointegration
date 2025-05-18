# region imports
from AlgorithmImports import *
import numpy as np
from collections import defaultdict
# endregion

class BayesianCointegrationPortfolioConstructionModel(PortfolioConstructionModel):
    """
    贝叶斯协整投资组合构建模型：
    - 从 AlphaModel 获取 insights。
    - 解析 Insight Tag 获取配对信息 (symbol2, beta)。
    - 为每个 Insight (代表一个配对操作) 生成独立的 PortfolioTarget 对。
      - Down: [PortfolioTarget(symbol1, -1), PortfolioTarget(symbol2, beta)]
      - Up:   [PortfolioTarget(symbol1, 1), PortfolioTarget(symbol2, -beta)]
      - Flat: [PortfolioTarget(symbol1, 0), PortfolioTarget(symbol2, 0)]
    - 不在模型内部聚合单个资产的权重，保留每个配对的独立目标。
    - 返回所有生成的 PortfolioTarget 对象的扁平列表。
    """
    def __init__(self, algorithm):
        super().__init__()
        self.algorithm = algorithm



    def CreateTargets(self, algorithm, insights):
        targets = []
        for i in range(0, len(insights), 2):
            insight1 = insights[i]
            insight2 = insights[i + 1]
            symbol1 = insight1.Symbol
            symbol2 = insight2.Symbol
            direction1 = insight1.Direction

            try:
                tag_parts = insight1.Tag.split('|')
                beta_mean = float(tag_parts[1])
            except:
                self.algorithm.Debug(f"[PC] 无法解析Insight Tag: {insight1.Tag}")
                continue

            targets += self._BuildPairTargets(symbol1, symbol2, direction1, insights, beta_mean)

        return targets



    def _BuildPairTargets(self, symbol1, symbol2, direction, insights, beta):
        """
        按照资金均分 + beta 对冲构建目标持仓, 使得资金控制在100%，整体没有杠杆
        """
        num_pairs = len(insights)
        if not np.isfinite(beta) or beta == 0 or num_pairs == 0:
            return []

        beta = abs(beta)
        L, S = 1.0, beta
        capital_per_pair = 1.0 / num_pairs
        scale = capital_per_pair / (L + S)

        # 多空方向决定最终权重正负
        if direction == InsightDirection.Up:
            self.algorithm.Debug(f"[PC] 建仓方向: Up, 协整对: symbol1:{symbol1.Value}{scale:.4f}, symbold2={symbol2.Value}{beta*scale:.4f}")
            return [PortfolioTarget(symbol1, scale), PortfolioTarget(symbol2, -scale * beta)]
        
        elif direction == InsightDirection.Down:
            self.algorithm.Debug(f"[PC] 建仓方向: Down, 协整对: symbol1:{symbol1.Value}{scale:.4f}, symbold2:{symbol2.Value}{beta*scale:.4f}")
            return [PortfolioTarget(symbol1, -scale), PortfolioTarget(symbol2, scale * beta)]
        
        elif direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC] 平仓: {symbol1.Value}/{symbol2.Value}")
            return [PortfolioTarget(symbol1, 0), PortfolioTarget(symbol2, 0)]
        
        else:
            return []



        
        
        






