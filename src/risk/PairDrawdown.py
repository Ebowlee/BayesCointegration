# region imports
from .PortfolioBaseRule import RiskRule
from typing import Tuple
# endregion


class PairDrawdownRule(RiskRule):
    """
    配对回撤风控规则 (v7.1.0 Intent Pattern重构)

    检测配对级别的回撤,如果浮亏超过阈值则触发平仓。

    触发条件:
    - 配对回撤 >= 阈值(默认15%)
    - 回撤定义: (HWM - current_pair_value) / HWM
    - pair_value = pnl + pair_cost (配对总价值 = 浮盈 + 保证金成本)

    v7.1.0变更:
    - 移除get_action()方法
    - Rule只负责检测,RiskManager负责生成CloseIntent(reason='DRAWDOWN')
    - cooldown由RiskManager在Intent执行后激活

    设计特点:
    - 配对专属计算: 使用tracked_qty和entry_price,避免Portfolio全局查询混淆
    - HWM自动追踪: 调用pair.get_pair_drawdown()时自动更新
    - 无需冷却期: 订单锁机制(tickets_manager.is_pair_locked)已防止重复提交
    - 最低优先级: priority=50,在PositionAnomaly(100)和HoldingTimeout(60)之后

    与PortfolioDrawdownRule的对比:
    - 层面: Pair vs Portfolio
    - HWM存储: Rule.pair_hwm_dict vs Rule.high_water_mark
    - HWM初始值: pair_cost(开仓时) vs initial_capital
    - 计算基础: pair_value(pnl+cost) vs TotalPortfolioValue
    - 触发动作: pair_close vs portfolio_liquidate_all
    - 冷却期: 无 vs 30天
    - 回撤公式: 相同 (HWM - current_value) / HWM

    配置示例:
    {
        'enabled': True,
        'priority': 50,
        'threshold': 0.15  # 15%回撤
    }

    使用场景:
    1. OnData循环检查所有pairs → PairDrawdownRule检测 → RiskManager生成Intent
    2. ExecutionManager执行平仓 → 清理亏损配对
    3. HWM在on_pair_closed()时自动清理
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化配对回撤规则

        Args:
            algorithm: QCAlgorithm实例
            config: 规则配置字典,包含'enabled','priority','threshold','action'
        """
        super().__init__(algorithm, config)

        # v6.9.4: HWM 追踪从 Pairs 迁移到 Rule (职责分离)
        # 设计原则: 风控状态应该由风控系统管理,而非配对实体
        self.pair_hwm_dict = {}  # {pair_id: hwm_pnl}


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发回撤风控 (v7.1.2: 新增per-pair cooldown检查)

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期 (v7.1.2新增)
        3. 获取配对当前 PnL 和保证金成本 (调用 pair.get_pair_pnl() 和 pair.get_pair_cost())
        4. 计算配对总价值: pair_value = pnl + pair_cost
        5. 更新该配对的 HWM (追踪 pair_value 峰值)
        6. 计算标准回撤: (HWM - current_pair_value) / HWM
        7. 与阈值比较

        Args:
            pair: Pairs对象,必须实现 get_pair_pnl() 和 get_pair_cost() 方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述(包含回撤比例、pair_value、HWM)

        标准回撤公式:
            drawdown = (HWM - current_value) / HWM
            - HWM: 历史最高配对价值 (pnl + pair_cost 的峰值)
            - current_value: 当前配对价值 (pnl + pair_cost)
            - 初始HWM: 开仓时的 pair_cost (此时pnl=0)

        与Portfolio回撤的对比:
            - Portfolio: (HWM_portfolio - TotalPortfolioValue) / HWM_portfolio
            - Pair: (HWM_pair_value - current_pair_value) / HWM_pair_value
            - 公式结构完全一致,只是追踪对象不同

        v7.1.2变更:
            - 新增per-pair cooldown检查,防止同一配对短期内重复触发

        示例:
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "配对回撤: 16.5% >= 15.0% (当前价值: $8,350, HWM: $10,000)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期 (v7.1.2新增)
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. 获取当前 PnL 和保证金成本 (Pairs 提供数据)
        pnl = pair.get_pair_pnl()
        pair_cost = pair.get_pair_cost()

        # 数据完整性检查
        if pnl is None or pair_cost is None or pair_cost <= 0:
            return False, ""

        # 4. 计算配对总价值
        pair_value = pnl + pair_cost

        # 5. 管理 HWM (Rule 的职责)
        pair_id = pair.pair_id

        # 初始化 HWM (开仓时为 pair_cost,此时 pnl=0)
        if pair_id not in self.pair_hwm_dict:
            self.pair_hwm_dict[pair_id] = pair_cost

        # 更新 HWM (追踪 pair_value 峰值)
        if pair_value > self.pair_hwm_dict[pair_id]:
            self.pair_hwm_dict[pair_id] = pair_value

        hwm = self.pair_hwm_dict[pair_id]

        # 6. 计算标准回撤
        drawdown = (hwm - pair_value) / hwm

        # 7. 判断是否触发
        threshold = self.config['threshold']
        if drawdown >= threshold:
            description = (
                f"配对回撤: {drawdown*100:.1f}% >= {threshold*100:.1f}% "
                f"(当前价值: ${pair_value:,.2f}, HWM: ${hwm:,.2f}, "
                f"PnL: ${pnl:,.2f}, 成本: ${pair_cost:,.2f})"
            )
            return True, description

        return False, ""


    def on_pair_closed(self, pair_id: tuple):
        """
        配对平仓后的清理回调 (v6.9.4 新增)

        职责:
        - 清理该配对的 HWM 状态
        - 避免内存泄漏 (长期运行策略的关键)

        调用时机:
        - main.py 在执行平仓后立即调用
        - ExecutionManager.handle_signal_closings() / handle_pair_risk_actions()

        Args:
            pair_id: 配对标识符元组 (symbol1, symbol2)

        示例:
            # main.py 中 (v7.0.0: Intent模式)
            intent = pair.get_close_intent(reason='STOP_LOSS')
            if intent:
                tickets = order_executor.execute_close(intent)
                if tickets:
                    tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)
                    # 订单提交后立即清理 HWM
                    self.risk_manager.pair_drawdown_rule.on_pair_closed(pair.pair_id)
        """
        if pair_id in self.pair_hwm_dict:
            del self.pair_hwm_dict[pair_id]
