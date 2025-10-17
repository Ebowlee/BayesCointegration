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


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发回撤风控

        检查流程:
        1. 检查规则是否启用
        2. 调用pair.get_pair_drawdown()获取回撤比例(自动更新HWM)
        3. 与阈值比较判断是否触发

        Args:
            pair: Pairs对象,必须实现get_pair_drawdown()和get_pair_pnl()方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述(包含回撤比例、PnL、HWM、成本)

        设计说明:
            - 完全复用Pairs.get_pair_drawdown()方法,遵循DRY原则
            - 该方法内部自动更新HWM和计算回撤
            - 与HoldingTimeoutRule/PositionAnomalyRule类似,避免重复实现逻辑

        示例:
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "配对回撤: 16.5% >= 15.0% (PnL: $-1,650.00, HWM: $500.00, 成本: $10,000.00)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 调用pair.get_pair_drawdown()获取回撤(自动更新HWM)
        drawdown = pair.get_pair_drawdown()
        if drawdown is None:
            return False, ""

        # 3. 获取阈值
        threshold = self.config['threshold']

        # 4. 判断是否触发
        if drawdown >= threshold:
            # 获取PnL用于描述
            pnl = pair.get_pair_pnl()
            description = (
                f"配对回撤: {drawdown*100:.1f}% >= {threshold*100:.1f}% "
                f"(PnL: ${pnl:,.2f}, HWM: ${pair.pair_hwm:,.2f}, "
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
