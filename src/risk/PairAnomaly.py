# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
from src.constants import PositionMode
# endregion


class PairAnomalyRule(RiskRule):
    """
    配对异常风控规则 

    检测配对持仓的异常状态,包括单边持仓和同向持仓。
    这些异常通常由订单部分成交、取消或拒绝导致。

    触发条件:
    - PARTIAL_LEG1: 只有第一腿有持仓,第二腿无持仓
    - PARTIAL_LEG2: 只有第二腿有持仓,第一腿无持仓
    - ANOMALY_SAME: 两腿持仓方向相同(都是多头或都是空头)
    - 移除get_action()方法
    - Rule只负责检测,RiskManager负责生成CloseIntent(reason='ANOMALY')
    - cooldown由RiskManager在Intent执行后激活

    设计特点:
    - 最高优先级: priority=100,异常持仓需要立即处理
    - 无需冷却期: 订单锁机制(tickets_manager.is_pair_locked)已防止重复提交
    - 复用基础设施: 直接调用pair.has_anomaly_position()方法,避免重复实现
    - 与TicketsManager协同: TicketsManager.get_anomaly_pairs()提供初步检测

    配置示例:
    {
        'enabled': True,
        'priority': 100
    }

    使用场景:
    1. OnOrderEvent检测到订单异常(Canceled/Invalid) → TicketsManager标记
    2. OnData循环检查pairs → PairAnomalyRule检测 → RiskManager生成Intent
    3. ExecutionManager执行平仓 → 清理异常持仓
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化配对异常规则

        Args:
            algorithm: QCAlgorithm实例
            config: 规则配置字典,只需包含'enabled'和'priority'
        """
        super().__init__(algorithm, config)


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否有异常持仓

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期
        3. 调用pair.has_anomaly()判断是否有异常
        4. 如果有异常,获取position_info详情
        5. 根据异常类型生成描述信息

        Args:
            pair: Pairs对象,必须实现has_anomaly_position()和get_position_info()方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示检测到异常,False表示正常
            - description: 详细描述(包含异常类型、持仓数量)

        设计说明:
            - 复用Pairs.has_anomaly_position()方法,遵循DRY原则
            - 该方法内部调用get_position_info()自动检测异常模式
            - 与HoldingTimeoutRule类似,避免重复实现检测逻辑
            - v7.1.2: 新增per-pair cooldown检查,防止同一配对短期内重复触发

        示例:
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "单边持仓LEG1: AAPL=100, MSFT=0")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. 调用pair.has_anomaly_position()检测异常(复用Pairs自带方法)
        if not pair.has_anomaly_position():
            return False, ""

        # 4. 获取持仓详情用于生成描述
        info = pair.get_position_info()
        mode = info['position_mode']
        qty1 = info['qty1']
        qty2 = info['qty2']

        # 5. 根据异常类型生成描述
        if mode == PositionMode.PARTIAL_LEG1:
            description = (f"单边持仓LEG1: {pair.symbol1}={qty1:+.0f}, " f"{pair.symbol2}=0")
        elif mode == PositionMode.PARTIAL_LEG2:
            description = (f"单边持仓LEG2: {pair.symbol1}=0, " f"{pair.symbol2}={qty2:+.0f}")
        elif mode == PositionMode.ANOMALY_SAME:
            description = (f"同向持仓: {pair.symbol1}={qty1:+.0f}, " f"{pair.symbol2}={qty2:+.0f}")
        else:
            # 防御性编程: 理论上不应该到达这里
            # 如果has_anomaly()返回True,mode必然是上述三种之一
            description = f"未知异常: mode={mode}, qty1={qty1:+.0f}, qty2={qty2:+.0f}"
            self.algorithm.Error(f"[PairAnomalyRule] 检测到未预期的异常模式: {mode}")

        return True, description
