# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairHoldingTimeoutRule(RiskRule):
    """
    持仓超时风控规则 (v7.1.0 Intent Pattern重构)

    检测配对持仓时间是否超过阈值。配对交易是短期均值回归策略,
    如果持仓超过max_days仍未回归,说明协整关系可能失效,应止损退出。

    触发条件:
    - 持仓天数 > max_days (从pair.pair_opened_time到当前时间)

    v7.1.0变更:
    - 移除get_action()方法
    - Rule只负责检测,RiskManager负责生成CloseIntent(reason='TIMEOUT')
    - cooldown由RiskManager在Intent执行后激活

    设计特点:
    - 无需冷却期: 订单锁机制(tickets_manager.is_pair_locked)已防止重复提交
    - 简单高效: 只需检查时间差,不涉及PnL计算
    - 优先级中等: priority=60,介于PairAnomaly(100)和PairDrawdown(50)之间

    配置示例:
    {
        'enabled': True,
        'priority': 60,
        'max_days': 30
    }

    使用示例:
    ```python
    # RiskManager中:
    triggered, desc = rule.check(pair)
    if triggered:
        intent = pair.get_close_intent(reason='TIMEOUT')  # RiskManager生成Intent
    ```
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化持仓超时规则

        Args:
            algorithm: QCAlgorithm实例
            config: 规则配置字典,必须包含'max_days'字段
        """
        super().__init__(algorithm, config)
        self.max_days = config['max_days']


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发持仓超时 (v7.1.2: 新增per-pair cooldown检查)

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期 (v7.1.2新增)
        3. 调用pair.get_pair_holding_days()获取持仓天数
        4. 判断是否超过max_days阈值

        Args:
            pair: Pairs对象,必须实现get_pair_holding_days()方法

        Returns:
            (is_triggered, description)
            - is_triggered: True表示超时,False表示未超时
            - description: 详细描述(包含持仓天数、阈值、开仓时间)

        设计说明:
            - 复用Pairs.get_pair_holding_days()方法,避免重复实现
            - 该方法内部使用algorithm.UtcTime,避免时区问题
            - 符合DRY原则,与Pairs.is_in_cooldown()保持一致
            - v7.1.2: 新增per-pair cooldown检查,防止同一配对短期内重复触发

        示例:
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "持仓超时: 已持仓35天 > 上限30天 (开仓时间: 2024-01-01)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期 (v7.1.2新增)
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. 获取持仓天数 (复用Pairs自带方法,避免时区问题)
        holding_days = pair.get_pair_holding_days()

        # 如果无法获取持仓天数(无持仓或未记录),不触发
        if holding_days is None:
            return False, ""

        # 4. 判断是否超时
        if holding_days > self.max_days:
            # 获取开仓时间用于日志 (如果存在)
            entry_time = getattr(pair, 'pair_opened_time', None)
            entry_time_str = entry_time.strftime('%Y-%m-%d') if entry_time else "未知"

            description = (f"持仓超时: 已持仓{holding_days}天 > " f"上限{self.max_days}天 " f"(开仓时间: {entry_time_str})")
            return True, description

        return False, ""
