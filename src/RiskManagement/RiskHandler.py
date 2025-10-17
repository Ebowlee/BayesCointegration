"""
RiskHandler - 风控执行器

职责:
- 执行Portfolio和Pair层面的风控响应
- 与pairs_manager, tickets_manager交互
- 处理订单提交和注册

设计原则:
- 职责单一: 只负责执行,不负责检测
- 依赖注入: 通过构造函数注入所需依赖
- 对称Portfolio和Pair: 统一的handle接口
"""

from AlgorithmImports import *
from src.Pairs import OrderAction


class RiskHandler:
    """
    风控执行器

    负责执行RiskManager检测到的风控动作,包括:
    - Portfolio层面: 全部清仓、减仓50%、行业再平衡等
    - Pair层面: 单个配对平仓

    设计特点:
    - 与RiskManager配合使用(检测与执行分离)
    - 依赖注入: algorithm, pairs_manager, tickets_manager
    - 完全对称的Portfolio和Pair处理接口
    """

    def __init__(self, algorithm, pairs_manager, tickets_manager):
        """
        初始化风控执行器

        Args:
            algorithm: QuantConnect算法实例
            pairs_manager: 配对管理器
            tickets_manager: 订单追踪管理器
        """
        self.algorithm = algorithm
        self.pairs_manager = pairs_manager
        self.tickets_manager = tickets_manager


    def handle_portfolio_risk_action(self, action: str, triggered_rules: list):
        """
        处理Portfolio层面风控动作

        Args:
            action: 风控动作 ('portfolio_liquidate_all'等)
            triggered_rules: 触发的规则列表 [(rule, description), ...]

        执行流程:
        1. 记录所有触发规则的详细日志
        2. 根据action字符串分发到具体处理方法
        3. 激活所有触发规则的冷却期

        注意:
        - 当前只实现'portfolio_liquidate_all'(全部清仓)
        - 其他action作为占位,未来实现
        """
        # 记录所有触发的规则
        for rule, description in triggered_rules:
            self.algorithm.Debug(f"[风控触发] {rule.__class__.__name__}: {description}")

        # 根据action执行相应操作
        if action == 'portfolio_liquidate_all':
            self.liquidate_all_positions()

        elif action == 'portfolio_reduce_exposure_50':
            # 未来实现: 减仓50%
            self.algorithm.Debug(f"[风控] 减仓50%模式(暂未实现)")

        elif action == 'portfolio_rebalance_sectors':
            # 未来实现: 行业再平衡
            self.algorithm.Debug(f"[风控] 行业再平衡模式(暂未实现)")

        else:
            self.algorithm.Debug(f"[风控] 未知动作: {action}")

        # 激活所有触发规则的冷却期
        for rule, _ in triggered_rules:
            rule.activate_cooldown()
            self.algorithm.Debug(f"[风控] {rule.__class__.__name__} 冷却至 {rule.cooldown_until}")


    def handle_pair_risk_actions(self, pair_risk_actions: dict):
        """
        处理Pair层面风控动作(对称Portfolio风控)

        Args:
            pair_risk_actions: {pair_id: (action, triggered_rules), ...}

        执行流程:
        1. 遍历所有触发风控的配对
        2. 检查订单锁定状态(防止重复下单)
        3. 根据action执行相应动作(当前只有'pair_close')
        4. 注册订单到tickets_manager
        5. 记录详细的风控日志

        设计特点:
        - 完全对称Portfolio风控的handle_portfolio_risk_action()
        - 职责单一: 只负责执行,不负责检测
        - 与RiskManager的check_all_pair_risks()配合使用
        """
        for pair_id, (action, triggered_rules) in pair_risk_actions.items():
            # 获取配对对象
            pair = self.pairs_manager.get_pair_by_id(pair_id)

            # 订单锁定检查(防止重复下单)
            if self.tickets_manager.is_pair_locked(pair_id):
                continue

            # 根据action执行相应操作
            if action == 'pair_close':
                # 风控触发平仓
                self.algorithm.Debug(f"[Pair风控] {pair_id} 触发平仓风控")
                tickets = pair.close_position()
                if tickets:
                    self.tickets_manager.register_tickets(pair_id, tickets, OrderAction.CLOSE)
                    # 记录触发的规则详情
                    for _, desc in triggered_rules:
                        self.algorithm.Debug(f"  └─ {desc}")

            else:
                # 未来可能有其他action类型
                self.algorithm.Debug(f"[Pair风控] 未知动作: {action}")


    def liquidate_all_positions(self):
        """
        清空所有持仓(仅通过pairs_manager,不使用Liquidate)

        执行流程:
        1. 获取所有有持仓的配对
        2. 检查订单锁定状态(is_pair_locked)
        3. 通过pair.close_position()平仓(保持订单追踪)
        4. 注册订单到tickets_manager
        5. 记录详细日志

        设计决策:
        - 不使用QC的Liquidate()方法,原因:
          1. 绕过pair.close_position()
          2. 绕过tickets_manager.register_tickets()
          3. 破坏订单追踪体系
          4. 可能导致重复下单
        - 只通过pairs_manager管理的配对进行平仓
        - 保持TicketsManager的订单追踪完整性
        """
        self.algorithm.Debug(f"[风控清仓] 开始清空所有持仓...")

        # 获取所有有持仓的配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()

        if not pairs_with_position:
            self.algorithm.Debug(f"[风控清仓] 无持仓,跳过")
            return

        closed_count = 0
        for pair in pairs_with_position.values():
            # 订单锁定检查(防止重复下单)
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                self.algorithm.Debug(f"[风控清仓] {pair.pair_id} 订单处理中,跳过")
                continue

            # 通过pair平仓(保持订单追踪)
            tickets = pair.close_position()
            if tickets:
                self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)
                closed_count += 1
                self.algorithm.Debug(f"[风控清仓] {pair.pair_id} 已提交平仓订单")

        self.algorithm.Debug(f"[风控清仓] 完成: 平仓{closed_count}/{len(pairs_with_position)}个配对")
