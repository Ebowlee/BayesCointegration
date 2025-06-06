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
        self.algorithm.Debug("[PortfolioConstruction] 初始化完成")



    def create_targets(self, algorithm, insights):
        targets = []
        num_pairs = len(self.algorithm.universeSelectionModel.cointegrated_pairs)
        self.algorithm.Debug(f"[PC] -- [CreateTargets] 当前持仓配对数量: {num_pairs}")
        
        expired_insights = self.algorithm.insights.remove_expired_insights(self.algorithm.utc_time)
        targets = [PortfolioTarget.Percent(self.algorithm, insight.Symbol, 0) for insight in expired_insights]

        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
            else:
                self.algorithm.Debug(f"[PC] -- [CreateTargets] 接收到未分组的信号: {insight.Symbol.Value}, 忽略")
        
        # 遍历每组 Insight
        for group_id, group in grouped_insights.items():
            if len(group) != 2:
                self.algorithm.Debug(f"[PC] -- [CreateTargets] 接收到 {len(group)} 个信号, 预期应为2, 跳过")
                continue
            insight1, insight2 = group
            symbol1, symbol2 = insight1.Symbol, insight2.Symbol
            direction = insight1.Direction  # 默认以 insight1 为主方向

            # 打印该组的 GroupId 和其包含的 Insight 简要信息
            insight_info = ", ".join([f"{ins.Symbol.Value}|{ins.Direction}" for ins in group])
            self.algorithm.Debug(f"[PC] -- [CreateTargets] 接收到信号组: {group_id}, 包含信号: {insight_info}")

            # 尝试解析 beta
            try:
                tag_parts = insight1.Tag.split('|')
                beta_mean = float(tag_parts[1])
            except Exception as e:
                self.algorithm.Debug(f"[PC] -- [CreateTargets] 无法从 Insight.tag 中解析 beta: {insight1.Tag}, 错误: {e}")
                continue

            # 构建配对目标持仓
            pair_targets = self._BuildPairTargets(symbol1, symbol2, direction, group, beta_mean, num_pairs)
            targets += pair_targets
        
        self.algorithm.Debug(f"[PC] -- [CreateTargets] 本轮生成 PortfolioTarget 数量: {len(targets)}")

        return targets



    def _BuildPairTargets(self, symbol1, symbol2, direction, insights, beta, num_pairs):
        """
        按照资金均分 + beta 对冲构建目标持仓, 使得资金控制在100%，整体没有杠杆
        """
        if not np.isfinite(beta) or beta == 0 or num_pairs == 0:
            return []

        beta = abs(beta)
        L, S = 1.0, beta
        capital_per_pair = 1.0 / num_pairs
        scale = capital_per_pair / (L + S)

        # 多空方向决定最终权重正负
        if direction == InsightDirection.Up:
            self.algorithm.Debug(f"[PC] -- [BuildPairTargets]: [{symbol1.Value}, {symbol2.Value}] | [UP, DOWN] | [{scale:.4f}, {-scale*beta:.4f}]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, scale), PortfolioTarget.Percent(self.algorithm, symbol2, -scale * beta)]
        
        elif direction == InsightDirection.Down:
            self.algorithm.Debug(f"[PC] -- [BuildPairTargets]: [{symbol1.Value}, {symbol2.Value}] | [DOWN, UP] | [{scale:.4f}, {scale*beta:.4f}]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, -scale), PortfolioTarget.Percent(self.algorithm, symbol2, scale * beta)]
        
        elif direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC] -- [BuildPairTargets]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT] | [0, 0]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, 0), PortfolioTarget.Percent(self.algorithm, symbol2, 0)]
        
        else:
            return []



        
        
        






