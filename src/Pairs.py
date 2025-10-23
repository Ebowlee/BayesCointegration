# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, Optional, Tuple
from src.TradeHistory import TradeSnapshot
from src.execution import OpenIntent, CloseIntent
# endregion


# ===== 常量定义 =====
class TradingSignal:
    """交易信号常量"""
    LONG_SPREAD = 'LONG_SPREAD'     # 做多价差(买入symbol1,卖出symbol2)
    SHORT_SPREAD = 'SHORT_SPREAD'   # 做空价差(卖出symbol1,买入symbol2)
    CLOSE = 'CLOSE'                 # 平仓信号
    STOP_LOSS = 'STOP_LOSS'         # 止损信号
    HOLD = 'HOLD'                   # 持有信号
    WAIT = 'WAIT'                   # 等待信号
    COOLDOWN = 'COOLDOWN'           # 冷却期
    NO_DATA = 'NO_DATA'             # 无数据

class PositionMode:
    """持仓模式常量(整合状态+方向)"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常做多价差 (qty1>0, qty2<0)
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常做空价差 (qty1<0, qty2>0)
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 只有第一腿
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 只有第二腿
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常:同向持仓

class OrderAction:
    """订单动作常量"""
    OPEN = 'OPEN'
    CLOSE = 'CLOSE'


class Pairs:
    """配对交易的核心数据对象"""

    # ===== 1. 初始化与参数管理 =====

    def __init__(self, algorithm, model_data, config):
        """
        从贝叶斯建模结果初始化
        model_data包含配对的统计参数和基础信息
        """
        # === 算法引用 ===
        self.algorithm = algorithm
        self.config = config  # 保存配置以供后续方法使用

        # === 基础信息 ===
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = (self.symbol1.Value, self.symbol2.Value)
        self.industry_group = model_data['industry_group']

        # === 统计参数(从贝叶斯建模获得) ===
        self.alpha_mean = model_data['alpha_mean']                              # 截距(对数空间)
        self.beta_mean = model_data['beta_mean']                                # 斜率(对数空间)
        self.residual_mean = model_data['residual_mean']                        # 残差均值(对数空间,理论上接近0)
        self.residual_std = model_data['residual_std']                          # 残差标准差(对数空间)
        self.quality_score = model_data['quality_score']                        # 配对质量分数

        # === 交易阈值 ===
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.stop_threshold = config['stop_threshold']

        # === 控制设置 ===
        self.cooldown_days = config['pair_cooldown_days']
        self.max_holding_days = config['max_holding_days']

        # === 保证金参数 ===
        self.margin_long = config['margin_requirement_long']
        self.margin_short = config['margin_requirement_short']

        # === 历史追踪 ===
        self.creation_time = algorithm.Time                                    # 首次创建时间
        self.reactivation_count = 0                                            # 重新激活次数(配对消失又出现)

        # === 时间追踪 ===
        self.pair_opened_time = None                                           # 配对开仓时间(双腿都成交的时刻)
        self.pair_closed_time = None                                           # 配对平仓时间(双腿都成交的时刻)

        # === 持仓追踪(OrderTicket-based,避免Portfolio全局查询混淆) ===
        self.tracked_qty1 = 0                                                  # 配对专属持仓追踪(symbol1)
        self.tracked_qty2 = 0                                                  # 配对专属持仓追踪(symbol2)

        # === 成本追踪(配对专属PnL计算基础) ===
        self.entry_price1 = None                                               # symbol1开仓均价
        self.entry_price2 = None                                               # symbol2开仓均价
        self.exit_price1 = None                                                # symbol1平仓价(None=持仓中, 有值=已平仓)
        self.exit_price2 = None                                                # symbol2平仓价(None=持仓中, 有值=已平仓)


    def update_params(self, new_pair) -> bool:
        """
        从新的Pairs对象更新统计参数(当配对重新出现时调用)

        更新策略:
            - 有持仓: 不更新,保持参数冻结(维持开仓时的决策基础)
            - 无持仓: 完全更新所有模型参数

        设计理念:
            - 持仓期间参数冻结,避免"参数漂移"导致信号混乱
            - 信号系统(Entry/Exit/Stop)已经能够处理beta变化风险
            - "让信号说话" - 不通过频繁调参来干预系统

        Returns:
            bool: True=更新成功, False=有持仓未更新
        """
        # 持仓检查:有持仓时不更新
        if self.has_position():
            self.algorithm.Debug(
                f"[Pairs] {self.pair_id} 有持仓,参数保持冻结 "
                f"(beta={self.beta_mean:.3f}, 开仓时间={self.pair_opened_time})"
            )
            return False

        # 无持仓时:更新所有贝叶斯模型参数
        self.alpha_mean = new_pair.alpha_mean
        self.beta_mean = new_pair.beta_mean
        self.residual_mean = new_pair.residual_mean
        self.residual_std = new_pair.residual_std
        self.quality_score = new_pair.quality_score

        # 记录重新激活
        self.reactivation_count += 1
        return True


    @classmethod
    def from_model_result(cls, algorithm, model_result: Dict, config: Dict) -> 'Pairs':
        """
        工厂方法：从贝叶斯建模结果创建 Pairs 对象

        设计理念（与 PairData.from_clean_data() 和 TradeSnapshot.from_pair() 保持一致）：
        - 封装创建逻辑：调用者无需了解构造函数参数细节
        - 语义清晰：明确表达"从建模结果创建"的意图
        - 扩展性：未来可添加其他工厂方法（from_dict, from_historical_data）

        技术细节：
        - cls 是 Pairs 类本身（Python 自动传递）
        - cls(...) 调用构造函数 __init__，创建并返回 Pairs 实例对象
        - 返回值是 Pairs 实例，可直接调用实例方法（get_signal, open_position 等）

        Args:
            algorithm: QCAlgorithm 实例
            model_result: BayesianModeler 输出的单个建模结果
                格式: {
                    'symbol1': Symbol, 'symbol2': Symbol,
                    'alpha_mean': float, 'beta_mean': float,
                    'residual_mean': float, 'residual_std': float,
                    'quality_score': float, 'industry_group': str
                }
            config: 配对交易配置字典（src/config.py 的 pairs_trading 部分）

        Returns:
            Pairs: 新创建的 Pairs 实例对象

        Example:
            # main.py 中调用
            for model_result in modeling_results:
                pair = Pairs.from_model_result(self, model_result, self.config.pairs_trading)
                # pair 是 Pairs 实例，可以调用实例方法
                intent = pair.get_open_intent(amount, data)
                if intent:
                    tickets = order_executor.execute_open(intent)

        与构造函数的对比：
            # 方式 1：直接调用构造函数（不推荐）
            pair = Pairs(self, model_result, self.config.pairs_trading)

            # 方式 2：通过类方法工厂（推荐）✅
            pair = Pairs.from_model_result(self, model_result, self.config.pairs_trading)

            优势：语义清晰、与项目其他值对象一致、便于扩展
        """
        return cls(algorithm, model_result, config)


    def on_position_filled(self, action: str, fill_time, tickets):
        """
        订单成交回调(由TicketsManager调用)

        触发时机: TicketsManager检测到配对的所有订单都已Filled时

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            fill_time: 最后一条腿成交的时间(确保两腿都已成交)
            tickets: List[OrderTicket] 成交的订单票据列表,用于提取实际成交数量

        技术说明:
            - OrderTicket: QuantConnect SDK 订单票据类
            - OrderStatus: QuantConnect SDK 订单状态枚举 (来自 AlgorithmImports)
              包括: Filled, Canceled, Invalid, PartiallyFilled 等
            - ticket.Status: 订单当前状态 (OrderStatus 枚举值)
            - ticket.QuantityFilled: 实际成交数量
            - ticket.AverageFillPrice: 平均成交价格
        """
        if action == OrderAction.OPEN:
            self.pair_opened_time = fill_time

            # 从OrderTicket提取实际成交数量和均价
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                        self.entry_price1 = ticket.AverageFillPrice
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
                        self.entry_price2 = ticket.AverageFillPrice

        elif action == OrderAction.CLOSE:
            self.pair_closed_time = fill_time

            # === 在清零之前捕获交易快照（传给TradeJournal） ===
            # 注意：必须在清零之前捕获，因为清零后数据丢失
            if hasattr(self.algorithm, 'trade_journal'):
                # 提取平仓价格并保存到实例变量
                # OrderStatus 来自 AlgorithmImports (QuantConnect SDK)
                for ticket in tickets:
                    if ticket is not None and ticket.Status == OrderStatus.Filled:
                        if ticket.Symbol == self.symbol1:
                            self.exit_price1 = ticket.AverageFillPrice
                        elif ticket.Symbol == self.symbol2:
                            self.exit_price2 = ticket.AverageFillPrice

                # 从订单Tag中解析平仓原因
                # Tag格式: "('AAPL', 'MSFT')_CLOSE_STOP_LOSS_20240101_093000"
                # 或旧格式: "('AAPL', 'MSFT')_CLOSE_20240101_093000"
                close_reason = 'CLOSE'  # 默认值
                if tickets and tickets[0] is not None:
                    tag = tickets[0].Tag
                    # 解析Tag: pair_id_CLOSE_reason_timestamp
                    parts = tag.split('_')
                    if len(parts) >= 4 and parts[2] == 'CLOSE':
                        # 新格式: 包含reason
                        # parts[0-1]: pair_id, parts[2]: 'CLOSE', parts[3]: reason, parts[4-5]: timestamp
                        potential_reason = parts[3]
                        # 验证是否为有效的平仓原因
                        valid_reasons = ['CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER']
                        if potential_reason in valid_reasons:
                            close_reason = potential_reason
 
                # 创建快照并记录
                # 职责分离：Pairs 负责计算，TradeSnapshot 负责存储
                if self.exit_price1 is not None and self.exit_price2 is not None:
                    # 计算保证金成本（Regulation T margin rates）
                    pair_cost = self.get_pair_cost()

                    # 计算盈亏（get_pair_pnl会自动判断使用exit_price还是实时价格）
                    pair_pnl = self.get_pair_pnl()

                    # 防御性检查：确保计算成功
                    if pair_cost is None:
                        self.algorithm.Debug(
                            f"[平仓警告] {self.pair_id} pair_cost计算失败，跳过交易记录"
                        )
                    else:
                        snapshot = TradeSnapshot.from_pair(
                            pair=self,
                            close_reason=close_reason,
                            entry_price1=self.entry_price1,
                            entry_price2=self.entry_price2,
                            exit_price1=self.exit_price1,  # 使用实例变量
                            exit_price2=self.exit_price2,  # 使用实例变量
                            qty1=self.tracked_qty1,        # 保持原始符号
                            qty2=self.tracked_qty2,        # 保持原始符号
                            open_time=self.pair_opened_time,
                            close_time=self.pair_closed_time,
                            pair_cost=pair_cost,          
                            pair_pnl=pair_pnl              
                        )
                        self.algorithm.trade_journal.record(snapshot)

            # 清零所有追踪变量
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
            self.entry_price1 = None
            self.entry_price2 = None
            self.exit_price1 = None  
            self.exit_price2 = None 


    # ===== 2. 基础数据访问(无依赖) =====

    def get_price(self, data):
        """
        从data slice获取最新价格
        返回: (price1, price2) 或 None

        安全检查:
        - symbol在data中存在
        - data[symbol]不为None (防止QuantConnect数据缺失)
        - data[symbol].Close有效且>0
        """
        # 增强检查: symbol存在且data不为None
        if (self.symbol1 in data and self.symbol2 in data and
            data[self.symbol1] is not None and data[self.symbol2] is not None):

            # 获取Close价格
            price1 = data[self.symbol1].Close
            price2 = data[self.symbol2].Close

            # 价格有效性检查(避免负价或零价)
            if price1 > 0 and price2 > 0:
                return (price1, price2)

        return None


    def get_position_info(self) -> Dict:
        """
        获取完整的持仓信息(一次获取,避免重复查询)
        使用tracked_qty避免Portfolio全局查询混淆

        返回所有持仓相关信息
        """
        portfolio = self.algorithm.Portfolio

        # 使用配对专属的tracked_qty(从OrderTicket提取的实际成交数量)
        qty1 = self.tracked_qty1
        qty2 = self.tracked_qty2

        # 市值仍需从Portfolio获取(需要当前价格)
        if qty1 != 0:
            value1 = abs(qty1 * portfolio[self.symbol1].Price)
        else:
            value1 = 0
        if qty2 != 0:
            value2 = abs(qty2 * portfolio[self.symbol2].Price)
        else:
            value2 = 0

        # 统一判断持仓模式(整合状态+方向)
        if qty1 == 0 and qty2 == 0:
            position_mode = PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            position_mode = PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            position_mode = PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            position_mode = PositionMode.PARTIAL_LEG1
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG1: qty1={qty1:+.0f}")
        elif qty1 == 0 and qty2 != 0:
            position_mode = PositionMode.PARTIAL_LEG2
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG2: qty2={qty2:+.0f}")
        else:  # 同向持仓
            position_mode = PositionMode.ANOMALY_SAME
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 同向持仓: qty1={qty1:+.0f}, qty2={qty2:+.0f}")

        return {'position_mode': position_mode, 'qty1': qty1, 'qty2': qty2, 'value1': value1, 'value2': value2}


    @property
    def position_mode(self):
        """
        获取当前持仓模式（避免重复代码）

        设计目标：
        - 消除 has_position(), has_normal_position(), has_anomaly() 中的重复代码
        - 提供清晰直观的接口：self.position_mode 比 self.get_position_info()['position_mode'] 更简洁
        - 遵循 DRY 原则：字典键访问封装为属性，避免 4 处重复

        实现细节：
        - 内部调用 get_position_info()['position_mode']
        - Portfolio.Price 是 O(1) 的字典查询（已被 QuantConnect 缓存）
        - 无需额外缓存机制（性能成本 ~0.001ms，复杂度不值得）

        Returns:
            PositionMode 常量之一：
            - NONE: 无持仓
            - LONG_SPREAD / SHORT_SPREAD: 正常持仓
            - PARTIAL_LEG1 / PARTIAL_LEG2 / ANOMALY_SAME: 异常持仓
        """
        return self.get_position_info()['position_mode']


    def get_pair_holding_days(self) -> Optional[int]:
        """
        获取持仓时长(天数) - 从开仓到现在

        Returns:
            持仓天数 或 None(无持仓或无开仓时间)
        """
        if not self.has_normal_position():
            return None

        # 直接访问开仓时间属性
        entry_time = self.pair_opened_time
        if entry_time is not None:
            return (self.algorithm.UtcTime - entry_time).days

        return None  # 无法获取入场时间


    def get_pair_frozen_days(self) -> Optional[int]:
        """
        获取冷却时长(天数) - 从平仓到现在

        与 get_pair_holding_days() 对称设计:
        - get_pair_holding_days(): 持仓天数 (从开仓到现在)
        - get_pair_frozen_days(): 冷却天数 (从平仓到现在)

        Returns:
            冷却天数 或 None(从未平仓)
        """
        if self.pair_closed_time is None:
            return None  # 从未平仓

        return (self.algorithm.UtcTime - self.pair_closed_time).days


    def get_pair_pnl(self) -> Optional[float]:
        """
        计算配对当前浮动盈亏（纯数据计算，无副作用）

        公式:
        - 当前市值 = qty1 * current_price1 + qty2 * current_price2
        - 开仓成本 = qty1 * entry_price1 + qty2 * entry_price2
        - PnL = 当前市值 - 开仓成本

        关键设计:
        - 使用tracked_qty避免Portfolio全局查询混淆
        - 使用entry_price而非Portfolio.AveragePrice(全局均价)
        - 空头的qty为负数,自动处理方向
        - 完全配对专属计算,即使symbol出现在多个配对中也不会混淆

        设计说明:
        - HWM追踪逻辑已迁移到 PairDrawdownRule
        - 纯函数设计（无状态修改），遵循函数式编程原则
        - 调用方: PairDrawdownRule, TradeSnapshot

        返回:
            浮动盈亏(美元) 或 None(无持仓或数据不完整)
        """
        # 必须有正常持仓
        if not self.has_normal_position():
            return None

        # 检查是否有开仓价格(防御性编程)
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        # 获取当前市场价格
        if self.exit_price1 is None or self.exit_price2 is None:
            # 持仓中: 使用实时价格(浮动PnL)
            portfolio = self.algorithm.Portfolio
            price1 = portfolio[self.symbol1].Price
            price2 = portfolio[self.symbol2].Price
        else:
            # 已平仓: 使用成交价格(最终PnL)
            price1 = self.exit_price1
            price2 = self.exit_price2

        # 计算当前市值(考虑方向: 多头为正,空头为负)
        current_value = (self.tracked_qty1 * price1 +
                        self.tracked_qty2 * price2)

        # 计算开仓成本(考虑方向)
        entry_value = (self.tracked_qty1 * self.entry_price1 +
                      self.tracked_qty2 * self.entry_price2)

        # PnL = 当前市值 - 开仓成本
        pnl = current_value - entry_value

        return pnl


    def get_pair_cost(self) -> Optional[float]:
        """
        计算配对总保证金占用（Total Margin Required）

        公式：
        - 多头腿：market_value * margin_requirement_long (0.5)
        - 空头腿：market_value * margin_requirement_short (1.5)

        金融原理：
        - Regulation T 保证金规则：
          * 多头：50% 保证金（买入 $10,000 需要 $5,000 保证金）
          * 空头：150% 保证金（卖空 $10,000 需要 $15,000 保证金 = $10,000 借券 + $5,000 保证金）
        - pair_cost 表示"实际占用的保证金"，而非"控制的市值"
        - 用于回撤率和收益率计算的分母

        防御性设计：
        - has_normal_position() 已确保只有 LONG_SPREAD 或 SHORT_SPREAD
        - 无需再次检查同向持仓（position_mode 已保证）

        调用方：
        - PairDrawdownRule.check()：计算回撤率
        - Pairs.on_position_filled(CLOSE)：创建 TradeSnapshot

        Returns:
            配对总保证金（美元）或 None（无持仓/数据不完整）

        Example:
            # LONG_SPREAD: qty1=+100, qty2=-100, price1=$50, price2=$50
            # market_value1 = 100*50 = $5,000 (多头)
            # market_value2 = 100*50 = $5,000 (空头)
            # margin1 = 5000 * 0.5 = $2,500 (多头保证金)
            # margin2 = 5000 * 1.5 = $7,500 (空头保证金)
            # pair_cost = 2500 + 7500 = $10,000 (总保证金占用)
        """
        # 基础检查（已包含同向排除）
        if not self.has_normal_position():
            return None

        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        # 获取保证金率
        margin_long = self.margin_long    # 0.5（多头50%）
        margin_short = self.margin_short  # 1.5（空头150%）

        # 计算市值
        market_value1 = abs(self.tracked_qty1 * self.entry_price1)
        market_value2 = abs(self.tracked_qty2 * self.entry_price2)

        # 根据 qty1 符号判断方向
        # has_normal_position() 已保证：
        # - qty1 > 0 → LONG_SPREAD  (qty2 < 0)
        # - qty1 < 0 → SHORT_SPREAD (qty2 > 0)
        if self.tracked_qty1 > 0:
            # LONG_SPREAD：symbol1 多头，symbol2 空头
            margin1 = market_value1 * margin_long   # 多头保证金
            margin2 = market_value2 * margin_short  # 空头保证金
        else:
            # SHORT_SPREAD：symbol1 空头，symbol2 多头
            margin1 = market_value1 * margin_short  # 空头保证金
            margin2 = market_value2 * margin_long   # 多头保证金

        return margin1 + margin2


    # ===== 3. 状态判断(依赖第2层) =====

    def has_position(self) -> bool:
        """检查是否有持仓（优化后：使用 @property）"""
        return self.position_mode != PositionMode.NONE


    def has_normal_position(self) -> bool:
        """检查是否有正常持仓（优化后：使用 @property）"""
        return self.position_mode in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]


    def has_anomaly_position(self) -> bool:
        """检查是否有异常持仓"""
        return self.position_mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]


    def get_pair_position_value(self) -> float:
        """获取当前持仓市值(包括部分持仓)"""
        info = self.get_position_info()
        return info['value1'] + info['value2']


    def is_in_cooldown(self) -> bool:
        """
        检查是否在冷却期内

        内部调用 get_pair_frozen_days() 避免重复逻辑

        Returns:
            True(在冷却期) / False(可以交易)
        """
        frozen_days = self.get_pair_frozen_days()
        if frozen_days is None:
            return False  # 从未平仓,不在冷却期

        return frozen_days < self.cooldown_days


    # ===== 4. 交易信号生成(依赖第2/3层) =====

    def get_zscore(self, data) -> Optional[float]:
        """
        计算Z-score,包含spread计算
        需要传入data来获取最新价格,返回Z-score值或None
        使用对数价格计算: log(price1) = alpha + beta * log(price2) + residual
        """
        # 获取价格
        prices = self.get_price(data)
        if prices is None:
            return None

        price1, price2 = prices

        # 计算对数空间的残差(与贝叶斯模型一致)
        log_residual = np.log(price1) - (self.alpha_mean + self.beta_mean * np.log(price2))

        # 计算Z-score
        if self.residual_std > 0:
            zscore = (log_residual - self.residual_mean) / self.residual_std
        else:
            zscore = 0

        return zscore


    def get_signal(self, data):
        """
        获取交易信号
        一步到位的接口,内部自动计算所需信息
        """
        # 先检查冷却期(仅在无持仓时检查)
        if not self.has_position() and self.is_in_cooldown():
            return TradingSignal.COOLDOWN

        # 内部计算zscore
        zscore = self.get_zscore(data)
        if zscore is None:
            return TradingSignal.NO_DATA

        # 内部检查持仓
        has_position = self.has_normal_position()

        # 生成信号
        if not has_position:
            # Z-score高,spread偏高,做空
            if zscore > self.entry_threshold:
                return TradingSignal.SHORT_SPREAD

            # Z-score低,spread偏低,做多
            elif zscore < -self.entry_threshold:
                return TradingSignal.LONG_SPREAD
            else:
                return TradingSignal.WAIT
        else:
            # 有持仓时的出场信号
            if abs(zscore) > self.stop_threshold:
                return TradingSignal.STOP_LOSS

            if abs(zscore) < self.exit_threshold:
                return TradingSignal.CLOSE

            return TradingSignal.HOLD


    # ===== 5. 意图生成(依赖第2/3/4层) =====

    def get_open_intent(self, amount_allocated: float, data):
        """
        生成开仓意图（意图生成与执行分离）

        设计理念:
        - 内部调用get_signal()自动检测开仓信号
        - 返回OpenIntent对象,交给OrderExecutor执行
        - 如果无开仓信号或数据不足,返回None

        执行流程:
        1. 调用get_signal()检测信号类型
        2. 如果不是LONG_SPREAD或SHORT_SPREAD,返回None
        3. 计算目标市值(调用calculate_leg_values)
        4. 获取当前价格
        5. 计算目标数量(整数股)
        6. 构建OpenIntent对象并返回

        Args:
            amount_allocated: 分配的资金金额
            data: 数据切片,用于获取价格和计算信号

        Returns:
            OpenIntent对象 或 None(无开仓信号或数据不足)

        使用示例(在ExecutionManager中):
            intent = pair.get_open_intent(amount_allocated, data)
            if intent:
                tickets = order_executor.execute_open(intent)
                tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.OPEN)

        与旧版open_position()的对比:
            旧版: pair.open_position(signal, amount, data) → [ticket1, ticket2]
            新版: pair.get_open_intent(amount, data) → Intent → executor.execute() → tickets
            优势: Pairs不再依赖algorithm.MarketOrder(),职责更清晰
        """
        # 自动检测信号
        signal = self.get_signal(data)

        if signal not in [TradingSignal.LONG_SPREAD, TradingSignal.SHORT_SPREAD]:
            return None  # 无开仓信号

        # 计算目标市值
        value1, value2 = self.calculate_leg_values(amount_allocated, signal, data)
        if value1 is None or value2 is None:
            return None  # 市值计算失败

        # 获取价格
        prices = self.get_price(data)
        if prices is None:
            return None  # 价格获取失败
        price1, price2 = prices

        # 计算数量
        if signal == TradingSignal.LONG_SPREAD:
            # 做多spread = 买入symbol1,卖出symbol2
            qty1 = int(value1 / price1)
            qty2 = -int(value2 / price2)
        else:  # SHORT_SPREAD
            # 做空spread = 卖出symbol1,买入symbol2
            qty1 = -int(value1 / price1)
            qty2 = int(value2 / price2)

        # 检查数量有效性
        if qty1 == 0 or qty2 == 0:
            return None  # 数量为0,无法开仓

        # 构建意图对象
        return OpenIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=qty1,
            qty2=qty2,
            signal=signal,
            tag=self.create_order_tag(OrderAction.OPEN)
        )


    def get_close_intent(self, reason='CLOSE'):
        """
        生成平仓意图（意图生成与执行分离）

        设计理念:
        - 获取当前持仓信息
        - 返回CloseIntent对象,交给OrderExecutor执行
        - 如果无持仓,返回None

        Args:
            reason: 平仓原因 (默认 'CLOSE')
                   可选值: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        Returns:
            CloseIntent对象 或 None(无持仓)

        使用示例(在ExecutionManager中):
            intent = pair.get_close_intent(reason='STOP_LOSS')
            if intent:
                tickets = order_executor.execute_close(intent)
                tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)

        与旧版close_position()的对比:
            旧版: pair.close_position(reason) → [ticket1, ticket2]
            新版: pair.get_close_intent(reason) → Intent → executor.execute() → tickets
            优势: Pairs不再依赖algorithm.MarketOrder(),职责更清晰

        设计说明:
            - reason参数会编码到tag中(便于TradeSnapshot解析)
            - 支持单边持仓(qty1或qty2为0时,executor会自动跳过)
        """
        # 获取当前持仓
        info = self.get_position_info()
        qty1 = info['qty1']
        qty2 = info['qty2']

        if qty1 == 0 and qty2 == 0:
            return None  # 无持仓

        # 构建意图对象
        return CloseIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=qty1,
            qty2=qty2,
            reason=reason,
            tag=self.create_order_tag(OrderAction.CLOSE, reason)
        )


    # ===== 6. 资金计算(依赖第2层) =====

    def calculate_leg_values(self, allocated_amount: float, signal: str, data):
        """
        从分配资金计算两腿购买力,按beta数量配比

        核心公式:
        1. X + Y = allocated_amount (资金约束)
        2. Qty_A = beta × Qty_B (数量配比约束)
           其中 Qty_A = (X/margin_rate_A)/Price_A
                Qty_B = (Y/margin_rate_B)/Price_B

        LONG_SPREAD (A做多0.5, B做空1.5):
            (X/0.5)/Price_A = beta × (Y/1.5)/Price_B
            => Y = allocated_amount × 3 × Price_B / (beta × Price_A + 3 × Price_B)

        SHORT_SPREAD (A做空1.5, B做多0.5):
            (X/1.5)/Price_A = beta × (Y/0.5)/Price_B
            => Y = allocated_amount × Price_B / (beta × 3 × Price_A + Price_B)

        参数:
            allocated_amount: 分配的投资资金金额
            signal: 交易信号 (LONG_SPREAD/SHORT_SPREAD)
            data: 数据切片(用于获取当前价格)

        返回:
            (value_A, value_B): A和B的目标购买市值, 计算失败返回 (None, None)
        """
        # 获取当前价格
        prices = self.get_price(data)
        if prices is None:
            return None, None
        price_A, price_B = prices

        # 避免除零
        if price_A <= 0 or price_B <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 价格异常: A={price_A}, B={price_B}")
            return None, None

        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1

        if signal == TradingSignal.LONG_SPREAD:
            # A做多(margin_long=0.5), B做空(margin_short=1.5)
            # Y = allocated_amount × 3 × Price_B / (beta × Price_A + 3 × Price_B)
            denominator = beta * price_A + 3 * price_B
            if denominator <= 0:
                return None, None

            Y = allocated_amount * 3 * price_B / denominator
            X = allocated_amount - Y

            # 市值 = 资金 / 保证金率
            value_A = X / self.margin_long    # X / 0.5
            value_B = Y / self.margin_short   # Y / 1.5

        else:  # SHORT_SPREAD
            # A做空(margin_short=1.5), B做多(margin_long=0.5)
            # Y = allocated_amount × Price_B / (beta × 3 × Price_A + Price_B)
            denominator = beta * 3 * price_A + price_B
            if denominator <= 0:
                return None, None

            Y = allocated_amount * price_B / denominator
            X = allocated_amount - Y

            # 市值
            value_A = X / self.margin_short   # X / 1.5
            value_B = Y / self.margin_long    # Y / 0.5

        # 安全检查: 资金分配合理性
        if X <= 0 or Y <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 资金分配异常: X={X:.2f}, Y={Y:.2f}")
            return None, None

        return value_A, value_B


    # ===== 7. 辅助方法(无依赖) =====

    def create_order_tag(self, action: str, reason: str = None):
        """
        创建标准化的订单Tag

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            reason: 平仓原因 (仅用于 CLOSE 动作)
                   可选值: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        返回格式:
            OPEN:  "('AAPL', 'MSFT')_OPEN_20240101_093000"
            CLOSE: "('AAPL', 'MSFT')_CLOSE_STOP_LOSS_20240101_093000"

        注意: 时间戳精确到秒,防止同一天内多次信号的Tag冲突
        """
        timestamp = self.algorithm.Time.strftime('%Y%m%d_%H%M%S')

        if action == OrderAction.CLOSE and reason:
            # 平仓时包含reason
            return f"{self.pair_id}_{action}_{reason}_{timestamp}"
        else:
            # 开仓时或没有reason时的标准格式
            return f"{self.pair_id}_{action}_{timestamp}"


    def get_planned_allocation_pct(self) -> float:
        """
        计算基于质量分数的计划分配比例
        纯计算方法,不进行任何业务逻辑判断

        Returns:
            计划分配比例 (min_position_pct 到 max_position_pct)
        """
        # 基于质量分数的线性插值计算
        min_pct = self.config['min_position_pct']
        max_pct = self.config['max_position_pct']
        return min_pct + self.quality_score * (max_pct - min_pct)