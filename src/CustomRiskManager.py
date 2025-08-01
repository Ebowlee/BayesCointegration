# region imports
from AlgorithmImports import *
from typing import List, Dict, Set, Optional
from datetime import datetime
# endregion


class CustomRiskManager:
    """
    自定义风控管理器
    
    独立于QuantConnect的RiskManagementModel框架，通过主策略的事件转发实现每日风控检查。
    
    主要功能:
    1. 持仓时间风控: 超过最大持仓天数强制平仓
    2. 止损风控: 亏损超过阈值强制平仓
    3. 止盈风控: 盈利超过阈值建议平仓（可选）
    4. 订单事件处理: 更新PairLedger的持仓状态
    
    工作流程:
    - 每日OnData时检查所有持仓配对的风控条件
    - 触发风控的配对会被标记，避免重复触发
    - 通过OnOrderEvent更新持仓状态，清理已平仓的风控记录
    
    配置参数:
    - max_holding_days: 最大持仓天数（默认60天）
    - max_loss_percent: 最大亏损比例（默认10%）
    - max_profit_percent: 最大盈利比例（默认30%，可选）
    - enable_take_profit: 是否启用止盈（默认False）
    """
    
    def __init__(self, algorithm, config: dict, pair_ledger):
        """
        初始化风控管理器
        
        Args:
            algorithm: 主算法实例
            config: 风控配置字典
            pair_ledger: 配对账本实例
        """
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        
        # 风控参数
        self.max_holding_days = config.get('max_holding_days', 60)
        self.max_loss_percent = config.get('max_loss_percent', 0.10)
        self.max_profit_percent = config.get('max_profit_percent', 0.30)
        self.enable_take_profit = config.get('enable_take_profit', False)
        
        # 风控记录 - 避免同一配对重复触发风控
        self.risk_triggered_pairs: Set[tuple] = set()
        
        # 统计信息
        self.risk_triggers = {
            'TIMEOUT': 0,
            'STOP_LOSS': 0,
            'TAKE_PROFIT': 0
        }
        
        self.algorithm.Debug(
            f"[CustomRisk] 初始化完成 - 最大持仓{self.max_holding_days}天, "
            f"止损{self.max_loss_percent*100}%, "
            f"止盈{self.max_profit_percent*100}%{'(启用)' if self.enable_take_profit else '(禁用)'}"
        )
    
    def on_data(self, data):
        """
        每日数据更新时的风控检查
        
        由主策略的OnData方法调用，确保每日执行
        
        Args:
            data: QuantConnect的数据切片
        """
        # 获取所有配对的风控数据
        risk_data = self.pair_ledger.get_risk_control_data()
        
        if not risk_data:
            return
        
        # 检查风控条件
        risk_alerts = self._check_all_risk_conditions(risk_data)
        
        # 处理风控警报
        if risk_alerts:
            self._handle_risk_alerts(risk_alerts)
    
    def on_order_event(self, order_event):
        """
        订单成交后更新状态
        
        由主策略的OnOrderEvent方法调用
        
        Args:
            order_event: QuantConnect的订单事件
        """
        if order_event.Status != OrderStatus.Filled:
            return
        
        # 更新PairLedger的持仓状态
        self.pair_ledger.update_position_status_from_order(order_event)
        
        # 清理已平仓配对的风控记录
        self._clean_risk_records()
    
    def _check_all_risk_conditions(self, risk_data: List[Dict]) -> List[Dict]:
        """
        检查所有风控条件
        
        Args:
            risk_data: 从PairLedger获取的风控数据列表
            
        Returns:
            List[Dict]: 触发风控的警报列表
        """
        risk_alerts = []
        
        for data in risk_data:
            pair_key = data['pair']
            pair_info = data['pair_info']
            
            # 跳过已触发风控的配对（避免重复）
            if pair_key in self.risk_triggered_pairs:
                continue
            
            # 1. 持仓超时检查
            if data['holding_days'] > self.max_holding_days:
                risk_alerts.append({
                    'type': 'TIMEOUT',
                    'pair': pair_key,
                    'pair_info': pair_info,
                    'reason': f"持仓{data['holding_days']}天，超过{self.max_holding_days}天限制",
                    'data': data
                })
            
            # 2. 止损检查
            elif data['total_pnl_percent'] < -self.max_loss_percent:
                risk_alerts.append({
                    'type': 'STOP_LOSS',
                    'pair': pair_key,
                    'pair_info': pair_info,
                    'reason': f"亏损{data['total_pnl_percent']:.2%}，触发止损线{-self.max_loss_percent:.0%}",
                    'data': data
                })
            
            # 3. 止盈检查（可选）
            elif self.enable_take_profit and data['total_pnl_percent'] > self.max_profit_percent:
                risk_alerts.append({
                    'type': 'TAKE_PROFIT',
                    'pair': pair_key,
                    'pair_info': pair_info,
                    'reason': f"盈利{data['total_pnl_percent']:.2%}，触发止盈线{self.max_profit_percent:.0%}",
                    'data': data
                })
        
        return risk_alerts
    
    def _handle_risk_alerts(self, risk_alerts: List[Dict]):
        """
        处理风控警报
        
        Args:
            risk_alerts: 风控警报列表
        """
        for alert in risk_alerts:
            symbol1, symbol2 = alert['pair']
            pair_info = alert['pair_info']
            
            # 1. 标记配对风控状态
            pair_info.risk_triggered = True
            pair_info.risk_type = alert['type']
            
            # 2. 记录触发的配对，避免重复
            self.risk_triggered_pairs.add(alert['pair'])
            
            # 3. 更新统计
            self.risk_triggers[alert['type']] += 1
            
            # 4. 输出风控日志
            self.algorithm.Debug(
                f"[CustomRisk] {alert['type']}: {symbol1.Value}-{symbol2.Value} | {alert['reason']}"
            )
            
            # 5. 输出详细信息（调试用）
            details = alert['data']['details']
            self.algorithm.Debug(
                f"[CustomRisk] 详情: {symbol1.Value}({details[symbol1.Value]['quantity']:+.0f}) "
                f"PnL={details[symbol1.Value]['unrealized_pnl']:.2f}, "
                f"{symbol2.Value}({details[symbol2.Value]['quantity']:+.0f}) "
                f"PnL={details[symbol2.Value]['unrealized_pnl']:.2f}, "
                f"总PnL={details['total_pnl']:.2f}({details['total_pnl_percent']:.2%})"
            )
            
            # 6. 执行平仓
            self._close_pair_positions(symbol1, symbol2, alert['type'])
    
    def _close_pair_positions(self, symbol1: Symbol, symbol2: Symbol, risk_type: str):
        """
        执行配对平仓
        
        Args:
            symbol1, symbol2: 配对的两只股票
            risk_type: 风控类型
        """
        tag = f"风控平仓:{risk_type}"
        
        # 平仓两边
        if self.algorithm.Portfolio[symbol1].Invested:
            self.algorithm.Liquidate(symbol1, tag)
        
        if self.algorithm.Portfolio[symbol2].Invested:
            self.algorithm.Liquidate(symbol2, tag)
    
    def _clean_risk_records(self):
        """
        清理已平仓配对的风控记录
        
        在订单成交后调用，移除不再持仓的配对记录
        """
        # 获取所有仍有持仓的配对
        active_pairs = {
            (pair_info.symbol1, pair_info.symbol2)
            for pair_info in self.pair_ledger.all_pairs.values()
            if pair_info.has_position
        }
        
        # 清理不再持仓的记录
        self.risk_triggered_pairs = self.risk_triggered_pairs.intersection(active_pairs)
    
    def get_statistics(self) -> Dict:
        """
        获取风控统计信息
        
        Returns:
            Dict: 各类风控触发次数统计
        """
        return {
            'triggers': self.risk_triggers.copy(),
            'current_monitored': len(self.risk_triggered_pairs)
        }