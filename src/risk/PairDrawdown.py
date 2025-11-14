# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairDrawdownRule(RiskRule):
    """
    配对回撤风控规则 (v7.14.0: 双层检测机制)

    检测配对级别的回撤,包括单次交易回撤和累计历史回撤,任一超过阈值则触发平仓。

    v7.14.0 双层检测机制:
    - **Layer 2 (累计历史回撤)**: 检查累计收益率 total_pnl_dollars / total_pair_cost
      - 触发条件: cumulative_return < -threshold (如 -0.11 < -0.08)
      - 目标: 防止"每次亏一点点,累计亏很多"的温水煮青蛙配对
      - 无最小交易次数限制 (只要有历史交易就检查)
      - 优先级: 高于Layer 1 (先检查)

    - **Layer 1 (单次交易回撤)**: 检查当前持仓回撤 (HWM - current_value) / HWM
      - 触发条件: drawdown >= threshold (如 0.10 >= 0.08)
      - 目标: 防止单次交易大幅浮亏
      - HWM追踪: 自动更新配对价值峰值

    - **统一阈值**: 两层共用threshold = 0.08 (8%)
    - **触发任一**: Layer 2或Layer 1任一触发即平仓

    设计特点:
    - 双层防护: 同时捕获短期风险和长期结构性问题
    - 统一阈值: 简化配置,单次和累计共用threshold参数
    - 优先级分层: Layer 2先检查,累计问题更严重
    - 配对专属计算: 使用tracked_qty和entry_price,避免Portfolio全局查询混淆
    - HWM自动追踪: Layer 1使用Rule.pair_hwm_dict追踪峰值
    - 订单锁保护: tickets_manager.is_pair_locked防止重复提交

    配置示例 (v7.14.0):
    {
        'enabled': True,
        'priority': 90,
        'threshold': 0.08,                    # 统一阈值: 单次+累计
        'enable_cumulative_check': True       # 启用Layer 2累计检测
    }

    使用场景:
    1. OnData循环检查所有pairs → PairDrawdownRule检测 (双层) → RiskManager生成Intent
    2. ExecutionManager执行平仓 → 清理回撤配对
    3. HWM在on_pair_closed()时自动清理
    4. 冷却期由Pairs.get_cooldown_days()统一管理(DRAWDOWN→180天)

    示例触发日志:
    - Layer 2: "配对回撤-累计: -11.5% <= -8.0% (5笔历史)"
    - Layer 1: "配对回撤-单次: 10.5% >= 8.0% (当前价值: $8,950, HWM: $10,000, ...)"
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
        检查配对是否触发回撤风控 (v7.14.0: 双层检测)

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期
        3. **Layer 2 (累计历史回撤检测)**: 检查累计收益率是否低于阈值
        4. **Layer 1 (单次交易回撤检测)**: 检查当前持仓回撤是否超过阈值

        Args:
            pair: Pairs对象,必须实现 get_pair_pnl(), get_pair_cost(), total_pnl_dollars, total_pair_cost 属性

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述(区分Layer 1/Layer 2触发)

        v7.14.0 双层检测机制:
        - **Layer 2 (累计)**: 检查历史累计收益率 total_pnl_dollars / total_pair_cost
          - 触发条件: cumulative_return < -threshold (如 -0.11 < -0.08)
          - 目标: 防止"每次亏一点点,累计亏很多"的温水煮青蛙配对
          - 无最小交易次数限制 (只要有历史交易就检查)

        - **Layer 1 (单次)**: 检查当前持仓回撤 (HWM - current_value) / HWM
          - 触发条件: drawdown >= threshold (如 0.10 >= 0.08)
          - 目标: 防止单次交易大幅浮亏

        - **优先级**: Layer 2 > Layer 1 (累计问题更严重,先检查先返回)
        - **统一阈值**: 两层共用config['threshold'] = 0.08

        示例:
            # Layer 2触发:
            (True, "配对回撤-累计: -11.5% <= -8.0% (5笔历史)")

            # Layer 1触发:
            (True, "配对回撤-单次: 10.5% >= 8.0% (当前价值: $8,950, HWM: $10,000, ...)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. Layer 2: 累计历史回撤检测 (v7.14.0)
        if self.config.get('enable_cumulative_check', False):
            # 前置条件: 必须有历史交易数据
            if pair.trade_count > 0 and pair.total_pair_cost > 0:
                # 计算累计收益率 (小数形式,如 -0.11 代表 -11%)
                cumulative_return = pair.total_pnl_dollars / pair.total_pair_cost

                # 判断累计亏损是否超过阈值 (使用统一阈值)
                threshold = self.config['threshold']
                if cumulative_return < -threshold:
                    # 触发Layer 2: 累计亏损超过阈值
                    description = (
                        f"配对回撤-累计: {cumulative_return*100:.1f}% <= "
                        f"-{threshold*100:.1f}% ({pair.trade_count}笔历史)"
                    )
                    return True, description

        # 4. Layer 1: 单次交易回撤检测 (现有逻辑)
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

        # 7. 判断是否触发 Layer 1
        threshold = self.config['threshold']
        if drawdown >= threshold:
            # v7.14.0: 区分Layer 1触发日志
            pnl_status = "盈利" if pnl > 0 else "亏损"
            description = (
                f"配对回撤-单次: {drawdown*100:.1f}% >= {threshold*100:.1f}% "
                f"(当前价值: ${pair_value:,.2f}, HWM: ${hwm:,.2f}, "
                f"PnL: ${pnl:,.2f}, 成本: ${pair_cost:,.2f}, 状态: {pnl_status})"
            )
            return True, description

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
