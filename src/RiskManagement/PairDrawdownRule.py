# region imports
from .base import RiskRule
from typing import Tuple
# endregion


class PairDrawdownRule(RiskRule):
    """
    配对回撤风控规则

    检测配对级别的回撤,如果浮亏超过阈值则触发平仓。

    触发条件:
    - 配对回撤 >= 阈值(默认15%)
    - 回撤定义: (HWM - current_pnl) / entry_cost

    响应动作:
    - 'pair_close': 正常平仓该配对

    设计特点:
    - 配对专属计算: 使用tracked_qty和entry_price,避免Portfolio全局查询混淆
    - HWM自动追踪: 调用pair.get_pair_drawdown()时自动更新
    - 无需冷却期: 订单锁机制(tickets_manager.is_pair_locked)已防止重复提交
    - 最低优先级: priority=50,在PositionAnomaly(100)和HoldingTimeout(60)之后

    与ExcessiveDrawdownRule的对比:
    - 层面: Pair vs Portfolio
    - HWM存储: Pairs对象内 vs Rule实例内
    - HWM初始值: 0(开仓时) vs initial_capital
    - 计算基础: 配对专属PnL vs TotalPortfolioValue
    - 触发动作: pair_close vs portfolio_liquidate_all
    - 冷却期: 无 vs 30天

    配置示例:
    {
        'enabled': True,
        'priority': 50,
        'threshold': 0.15,  # 15%回撤
        'action': 'pair_close'
    }

    使用场景:
    1. OnData循环检查所有pairs → PairDrawdownRule检测 → 返回pair_close
    2. main.py执行平仓 → 清理亏损配对
    3. HWM在on_position_filled(CLOSE)时自动重置
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
        检查配对是否触发回撤风控 (v6.9.4: Rule 自己管理 HWM)

        检查流程:
        1. 检查规则是否启用
        2. 获取配对当前 PnL (调用 pair.get_pair_pnl())
        3. 更新该配对的 HWM (Rule 内部状态)
        4. 计算回撤并与阈值比较

        Args:
            pair: Pairs对象,必须实现 get_pair_pnl() 方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述(包含回撤比例、PnL、HWM、成本)

        设计变化 (v6.9.4):
            - HWM 从 Pairs.pair_hwm 迁移到 Rule.pair_hwm_dict
            - Pairs 只提供数据 (get_pair_pnl),Rule 负责风控逻辑
            - 遵循 "检测者管理状态,被检测者提供数据" 原则

        示例:
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "配对回撤: 16.5% >= 15.0% (PnL: $-1,650.00, HWM: $500.00, 成本: $10,000.00)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 获取当前 PnL (Pairs 提供数据)
        pnl = pair.get_pair_pnl()
        if pnl is None or pair.entry_cost <= 0:
            return False, ""

        # 3. 管理 HWM (Rule 的职责)
        pair_id = pair.pair_id

        # 初始化 HWM (开仓时为 0)
        if pair_id not in self.pair_hwm_dict:
            self.pair_hwm_dict[pair_id] = 0.0

        # 更新 HWM
        if pnl > self.pair_hwm_dict[pair_id]:
            self.pair_hwm_dict[pair_id] = pnl

        hwm = self.pair_hwm_dict[pair_id]

        # 4. 计算回撤
        drawdown = (hwm - pnl) / pair.entry_cost

        # 5. 判断是否触发
        threshold = self.config['threshold']
        if drawdown >= threshold:
            description = (
                f"配对回撤: {drawdown*100:.1f}% >= {threshold*100:.1f}% "
                f"(PnL: ${pnl:,.2f}, HWM: ${hwm:,.2f}, "
                f"成本: ${pair.entry_cost:,.2f})"
            )
            return True, description

        return False, ""


    def get_action(self) -> str:
        """
        获取响应动作

        Returns:
            'pair_close': 正常平仓动作(统一动作,无强制清算)
        """
        return self.config.get('action', 'pair_close')


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
            # main.py 中
            tickets = pair.close_position(reason='STOP_LOSS')
            if tickets:
                # 订单提交后立即清理 HWM
                self.risk_manager.pair_drawdown_rule.on_pair_closed(pair.pair_id)
        """
        if pair_id in self.pair_hwm_dict:
            del self.pair_hwm_dict[pair_id]
