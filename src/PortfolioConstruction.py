# region imports
from AlgorithmImports import *
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

        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
        
        # 遍历每组 Insight
        for group_id, group in grouped_insights.items():
            if len(group) != 2:
                self.algorithm.Debug(f"[PC] 接收到 {len(group)} 个信号, 预期应为2, 跳过")
                continue
            insight1, insight2 = group
            symbol1, symbol2 = insight1.Symbol, insight2.Symbol
            direction = insight1.Direction  

            # 尝试解析 beta_mean
            try:
                tag_parts = insight1.Tag.split('|')
                beta_mean = float(tag_parts[2]) 
                num = int(tag_parts[4])
            except Exception as e:
                self.algorithm.Debug(f"[PC] 无法解析 beta: {insight1.Tag}, 错误: {e}")
                continue

            # 构建配对目标持仓
            pair_targets = self._BuildPairTargets(symbol1, symbol2, direction, beta_mean, num)
            targets += pair_targets
        
        self.algorithm.Debug(f"[PC] 生成 【{len(targets)/2:.0f}】 组 PortfolioTarget")
        return targets



    def _BuildPairTargets(self, symbol1, symbol2, direction, beta, num):
        """
        按照资金均分 + beta 对冲构建目标持仓, 使得资金控制在100%，整体没有杠杆
        """
        L, S = 1.0, abs(beta)  
        capital_per_pair = 1.0 / num

        # 多空方向决定最终权重正负，这里使用固定保证金率0.5，实际使用时需要根据实际情况调整
        if direction == InsightDirection.Up and self.can_short(symbol2):
            margin = 0.5
            scale = capital_per_pair / (L + S*margin)
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [BUY, SELL] | [{scale:.4f}, {-scale*beta:.4f}]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, scale), PortfolioTarget.Percent(self.algorithm, symbol2, -scale * beta)] 
        
        elif direction == InsightDirection.Down and self.can_short(symbol1):
            margin = 0.5
            scale = capital_per_pair / (L + S*margin)   
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [SELL, BUY] | [{-scale:.4f}, {scale*beta:.4f}]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, -scale), PortfolioTarget.Percent(self.algorithm, symbol2, scale * beta)] 
        
        elif direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
            return [PortfolioTarget.Percent(self.algorithm, symbol1, 0), PortfolioTarget.Percent(self.algorithm, symbol2, 0)]
        
        else:
            self.algorithm.Debug(f"[PC]: 无法做空，跳过配对 [{symbol1.Value}, {symbol2.Value}]")
            return []



    # 检查是否可以做空（回测环境所有股票都可以做空，实盘时需要检测）
    def can_short(self, symbol: Symbol) -> bool:
        # security = self.algorithm.Securities[symbol]
        # shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
        # return shortable is not None and shortable > 0
        return True
  


        
        
        






