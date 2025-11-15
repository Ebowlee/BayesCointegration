# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairDrawdownRule(RiskRule):
    """
    配对回撤风控规则 (v7.27.0: 单一职责 - 只检测单次回撤)

    检测配对当前持仓从历史峰值(HWM)的回撤比例,防止单次交易大幅浮亏。

    核心概念:
    - **回撤 (Drawdown)**: 从历史最高价值下跌的比例
    - **计算公式**: drawdown = (HWM - current_value) / HWM
    - **HWM追踪**: 动态追踪配对价值峰值 (pair_value = pnl + cost)
    - **与亏损区别**: 回撤基于HWM(动态峰值),亏损基于cost(固定成本)

    功能:
    - 检测当前持仓回撤是否超过阈值(默认5%)
    - 触发后由RiskManager生成CloseIntent
    - 支持可配置冷却期(默认30天)
    - 优先级80 (低于PairCumulativeLossRule的90)

    配置参数:
    - enabled: 是否启用(默认True)
    - priority: 优先级(默认80)
    - threshold: 回撤阈值(默认0.05,即5%)
    - cooldown_days: 冷却期天数(默认30天)  # v7.28.0新增

    设计特点:
    - 概念纯粹: 只检测单次回撤,累计亏损由PairCumulativeLossRule负责
    - 独立阈值: 与累计亏损(8%)分离配置
    - HWM自动追踪: Rule.pair_hwm_dict追踪每个配对的峰值
    - 配对专属计算: 使用Pairs提供的pnl和cost
    - 平仓清理: on_pair_closed()自动清理HWM

    使用示例:
    ```python
    # 在RiskManager中
    config = self.config.risk_management['pair_rules']['pair_drawdown']
    rule = PairDrawdownRule(algorithm, config)

    intent = rule.check(pair)
    if intent:
        # RiskManager生成CloseIntent
        # ExecutionManager执行Intent后激活cooldown
        # 调用on_pair_closed()清理HWM
    ```

    触发场景示例:
    - HWM: $16,000 (历史峰值)
    - 当前价值: $15,200 (pnl + cost)
    - 回撤: 5.0%
    - 触发日志: "配对回撤-单次: 5.0% >= 5.0% (当前价值: $15,200, HWM: $16,000)"
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
        检查配对是否触发回撤风控 (v7.27.0: 只检测单次回撤)

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期
        3. 检查当前持仓回撤是否超过阈值

        Args:
            pair: Pairs对象,必须实现 get_pair_pnl(), get_pair_cost() 方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述

        检测逻辑:
        - 计算当前持仓回撤: (HWM - current_value) / HWM
        - 触发条件: drawdown >= threshold (如 0.05 表示5%)
        - 目标: 防止单次交易大幅浮亏

        触发示例:
            (True, "配对回撤-单次: 5.2% >= 5.0% (当前价值: $15,168, HWM: $16,000, ...)")

        未触发原因:
            - 规则未启用
            - 配对在冷却期
            - 数据不完整(pnl/cost为None)
            - 回撤未达阈值(如3% < 5%)
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. 单次交易回撤检测
        # 获取当前 PnL 和保证金成本 (Pairs 提供数据)
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

        # 7. 获取阈值
        threshold = self.config['threshold']

        # 智能日志: 只在触发或接近阈值时打印(减少噪音)
        warning_threshold = threshold * 0.8  # 警告线: 阈值的80%

        # 8. 判断是否触发
        if drawdown >= threshold:
            # 触发: 单次回撤超过阈值
            pnl_status = "盈利" if pnl > 0 else "亏损"
            description = (
                f"配对回撤-单次: {drawdown*100:.1f}% >= {threshold*100:.1f}% "
                f"(当前价值: ${pair_value:,.2f}, HWM: ${hwm:,.2f}, "
                f"PnL: ${pnl:,.2f}, 成本: ${pair_cost:,.2f}, 状态: {pnl_status})"
            )
            # v7.28.2: 移除重复打印,统一由RiskManager打印
            return True, description

        # 接近阈值时打印警告(警告线到阈值之间)
        elif drawdown >= warning_threshold:
            self.algorithm.Debug(
                f"[Pair风控] PairDrawdownRule 警告: 回撤={drawdown*100:.2f}% "
                f"(接近阈值{threshold*100:.0f}%, HWM=${hwm:,.0f}, 当前=${pair_value:,.0f})"
            )

        # 正常情况: 静默(不打印,减少日志噪音)
        return False, ""


    def on_pair_closed(self, pair_id: tuple):
        """
        配对平仓后的清理回调 

        职责:
        - 清理该配对的 HWM 状态
        - 避免内存泄漏 (长期运行策略的关键)

        调用时机:
        - main.py 在执行平仓后立即调用
        - ExecutionManager.handle_normal_close_intents() / handle_pair_risk_intents()

        Args:
            pair_id: 配对标识符元组 (symbol1, symbol2)

        示例:
            # main.py 中
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
