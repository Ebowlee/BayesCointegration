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
        
        # 获取并清理过期 insight，并生成平仓目标
        expired_insights = self.algorithm.insights.remove_expired_insights(self.algorithm.utc_time)
        targets = [PortfolioTarget(insight.Symbol, 0) for insight in expired_insights]
        
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
            else:
                self.algorithm.Debug(f"[PC] 未分组的 Insight: {insight.Symbol.Value}, 忽略")
        
        # 遍历每组 Insight
        for group_id, group in grouped_insights.items():
            if len(group) != 2:
                self.algorithm.Debug(f"[PC] GroupId {group_id} 包含 {len(group)} 个 Insight, 预期应为2, 跳过")
                continue
            insight1, insight2 = group
            symbol1, symbol2 = insight1.Symbol, insight2.Symbol
            direction = insight1.Direction  # 默认以 insight1 为主方向

            # 打印该组的 GroupId 和其包含的 Insight 简要信息
            insight_info = ", ".join([f"{ins.Symbol.Value}|{ins.Direction}" for ins in group])
            self.algorithm.Debug(f"[PC] 处理 GroupId: {group_id}, 包含的 Insights: {insight_info}")

            # 尝试解析 beta
            try:
                tag_parts = insight1.Tag.split('|')
                beta_mean = float(tag_parts[1])
            except Exception as e:
                self.algorithm.Debug(f"[PC] 无法从 Insight Tag 中解析 beta: {insight1.Tag}, 错误: {e}")
                continue

            # 构建配对目标持仓
            pair_targets = self._BuildPairTargets(symbol1, symbol2, direction, group, beta_mean)
            targets += pair_targets
        
        self.algorithm.Debug(f"[PC] 本轮共生成 PortfolioTarget 数量: {len(targets)}")

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
            self.algorithm.Debug(f"[PC] 协整对建仓:UP {symbol1.Value}|{scale:.4f}, DOWN {symbol2.Value}|{beta*scale:.4f}, beta={beta:.4f}")
            return [PortfolioTarget(symbol1, scale), PortfolioTarget(symbol2, -scale * beta)]
        
        elif direction == InsightDirection.Down:
            self.algorithm.Debug(f"[PC] 协整对建仓:DOWN {symbol1.Value}|{scale:.4f}, UP {symbol2.Value}|{beta*scale:.4f}, beta={beta:.4f}")
            return [PortfolioTarget(symbol1, -scale), PortfolioTarget(symbol2, scale * beta)]
        
        elif direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC] 平仓: {symbol1.Value}|0, {symbol2.Value}|0")
            return [PortfolioTarget(symbol1, 0), PortfolioTarget(symbol2, 0)]
        
        else:
            return []



        
        
        






