# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, Optional, Tuple
from src.execution import OpenIntent, CloseIntent
# v7.10.6: 常量已移至config.constants统一管理，不再需要constants.py
# endregion


class PositionMode:
    """持仓模式常量 - 避免魔法字符串"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常多头价差
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常空头价差
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 异常: 仅LEG1持仓
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 异常: 仅LEG2持仓
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常: 同向持仓


class Pairs:
    """
    配对交易的核心数据对象

    核心职责:
    - 数据提供者: 持仓追踪、价格计算、成本计算
    - 信号生成器: Z-score计算、交易信号生成
    - 意图生成器: 开仓/平仓意图编码
    - 历史追踪者: 交易统计、Z-score追踪(信号/开仓/平仓三阶段)

    不负责: 风险检查、资金分配、订单执行(由RiskManager/ExecutionManager/OrderExecutor负责)
    """

    # ===== 1. 初始化与参数管理 =====

    @classmethod
    def from_model_result(cls, algorithm, model_result: Dict, config) -> 'Pairs':
        """
        工厂方法：从贝叶斯建模结果创建 Pairs 对象

        设计理念（与 PairData.from_clean_data() 保持一致）：
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
            config: 配对交易配置对象（PairsTradingConfig dataclass from src/config.py）

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
        # 创建Pairs对象
        pair = cls(algorithm, model_result, config)

        # v7.12.0: 提取行业代码用于行业配额管理
        symbol1 = model_result['symbol1']
        try:
            pair.industry_code = algorithm.Securities[symbol1].Fundamentals.AssetClassification.MorningstarIndustryGroupCode
        except (AttributeError, KeyError):
            algorithm.Debug(f"[Pairs] 警告: 无法获取{symbol1}的行业代码", 1)
            pair.industry_code = None

        return pair


    def __init__(self, algorithm, model_data, config):
        """
        从贝叶斯建模结果初始化
        model_data包含配对的统计参数和基础信息
        """
        # === 算法引用 ===
        self.algorithm = algorithm
        self.config = config

        # === 基础信息 ===
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = (self.symbol1.Value, self.symbol2.Value)
        self.industry_group = model_data['industry_group']
        self.industry_code = None  # v7.12.0: MorningstarIndustryGroupCode (在from_model_result中填充)
        self.industry_quota_tier = None  # v7.32.0: 行业配额档次 ('tier0'/'tier1'/'tier2'/'tier3'/'tier4', 在PairSelector中填充)

        # === 统计参数(从贝叶斯建模获得) ===
        self.alpha_mean = model_data['alpha_mean']                              # 截距(对数空间)
        self.beta_mean = model_data['beta_mean']                                # 斜率(对数空间)
        self.residual_mean = model_data['residual_mean']                        # 残差均值(对数空间,理论上接近0)
        self.residual_std = model_data['residual_std']                          # 残差标准差(对数空间)
        self.quality_score = model_data['quality_score']                        # 配对质量分数
        self.half_life = model_data.get('half_life')                            # v7.11.0: 半衰期天数(供自适应持仓超时使用)
        self.half_life_std = model_data.get('half_life_std', 0)                 # v7.13.0: 半衰期不确定性(标准差)

        # === 交易阈值 (改良C方案 - 从pairs_trading统一读取) ===
        self.entry_threshold_lower = config.entry_threshold_lower              # 1.2σ
        self.entry_threshold_upper = config.entry_threshold_upper              # 1.8σ
        self.exit_threshold = config.exit_threshold                            # 0.3σ
        self.stop_loss_threshold = config.stop_loss_threshold                  # 2.3σ

        # v7.10.6: 冷却天数已移至config.constants.close_reasons，通过get_cooldown_days()动态查询

        # === 保证金参数 ===
        self.margin_long = config.margin_requirement_long
        self.margin_short = config.margin_requirement_short

        # === 历史追踪 ===
        self.creation_time = algorithm.Time                                    # 首次创建时间
        self.reactivation_count = 0                                            # 重新激活次数(配对消失又出现)

        # === 交易历史统计 (已平仓交易 - 加权平均累计) ===
        self.trade_count = 0                                                   # 历史总交易次数
        self.win_count = 0                                                     # 历史盈利次数
        self.realized_pnl = 0.0                                                # 已实现PnL (已平仓交易累计,加权平均分子)
        self.realized_cost = 0.0                                               # 已实现成本 (已平仓交易累计,加权平均分母)

        # === 时间追踪 ===
        self.pair_opened_time = None                                           # 配对开仓时间(双腿都成交的时刻)
        self.pair_closed_time = None                                           # 配对平仓时间(双腿都成交的时刻)
        self.last_close_reason = None                                          # 最后平仓原因 (参见 config.constants['close_reasons'])

        # === 交易质量追踪 (三阶段Z-score用于事后分析) ===
        self.entry_zscore = None                                               # 信号触发时Z-score(分析决策质量)
        self.fill_zscore_open = None                                           # 开仓成交时Z-score(分析执行滑点)
        self.fill_zscore_close = None                                          # 平仓成交时Z-score(分析退出质量)

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


    def set_industry_quota_tier(self, tier: str) -> None:
        """
        设置行业配额档次 (v7.32.0: 用于PairSelector调用)

        Args:
            tier: 行业配额档次 ('tier0'/'tier1'/'tier2'/'tier3'/'tier4')

        Usage:
            在PairSelector.select()中调用:
            ```python
            tier = industry_quotas.get(str(pair.industry_code), {}).get('tier', 'tier0')
            pair.set_industry_quota_tier(tier)
            ```

        设计理念:
            - 分离关注点: PairSelector负责设置tier, Pairs负责使用tier
            - 延迟绑定: tier在创建后设置,而非构造时传入(避免from_model_result参数膨胀)
            - 默认容错: 如果未设置,get_planned_allocation_pct()使用tier0兜底
        """
        self.industry_quota_tier = tier


    # ===== 2. 状态查询 =====

    # 2A. 实时数据查询

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

        # 市价仍需从Portfolio获取(需要当前价格)
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


    # 3B. 财务计算

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
        - 调用方: PairDrawdownRule, TradeAnalyzer

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
        current_value = (self.tracked_qty1 * price1 + self.tracked_qty2 * price2)

        # 计算开仓成本
        entry_value = (self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2)

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
        - TradeAnalyzer.analyze_trade()：计算交易成本

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


    def get_net_exposure(self) -> Optional[float]:
        """
        计算净敞口 (Dollar Net Exposure) - v7.40.0

        公式:
            Net Exposure = value1 + value2
            - 正值: 净多头敞口 (市场涨我赚)
            - 负值: 净空头敞口 (市场跌我赚)
            - 0: 完美对冲

        价格来源:
            - 始终使用实时价格 (Portfolio.Price)
            - 设计理由: "exposure"语义是当前持仓的市场暴露,不是历史回顾

        应用场景:
            - 行业级统计: IndustryStats 聚合净敞口
            - 风险监控: 检查单边敞口风险
            - 调用前提: 仅在 has_position()=True 时调用

        Returns:
            float: 净敞口金额 (美元), None表示无持仓或数据异常

        Example:
            # LONG_SPREAD: qty1=+100, qty2=-100, price1=$50, price2=$48
            # value1 = +100 * 50 = $5,000 (多头)
            # value2 = -100 * 48 = -$4,800 (空头)
            # net_exposure = 5000 + (-4800) = $200 (净多头)
        """
        if not self.has_normal_position():
            return None

        # 使用实时价格 (v7.40.0修正: 移除exit_price逻辑)
        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        # 计算两腿市值 (带符号)
        val1 = self.tracked_qty1 * price1
        val2 = self.tracked_qty2 * price2

        # 净敞口 = 两腿市值之和
        return val1 + val2


    def get_gross_exposure(self) -> Optional[float]:
        """
        计算总敞口 (Dollar Gross Exposure) - v7.40.0

        公式:
            Gross Exposure = |value1| + |value2|

        物理含义:
            - 衡量配对的总市值规模 (不考虑方向)
            - 作为漂移率计算的分母

        价格来源:
            - 始终使用实时价格 (Portfolio.Price)
            - 设计理由: "exposure"语义是当前持仓的市场暴露,不是历史回顾

        应用场景:
            - 行业级统计: IndustryStats 聚合总敞口
            - 漂移率计算: drift = net_exposure / gross_exposure
            - 调用前提: 仅在 has_position()=True 时调用

        Returns:
            float: 总敞口金额 (美元), None表示无持仓或数据异常

        Example:
            # LONG_SPREAD: qty1=+100, qty2=-100, price1=$50, price2=$48
            # value1 = +100 * 50 = $5,000 (多头)
            # value2 = -100 * 48 = -$4,800 (空头)
            # gross_exposure = |5000| + |-4800| = $9,800 (总敞口)
        """
        if not self.has_normal_position():
            return None

        # 使用实时价格 (v7.40.0修正: 移除exit_price逻辑)
        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        # 计算两腿市值 (带符号)
        val1 = self.tracked_qty1 * price1
        val2 = self.tracked_qty2 * price2

        # 总敞口 = 两腿市值绝对值之和
        return abs(val1) + abs(val2)


    # ===== 3. 核心算力 =====

    def get_hedge_drift(self) -> Optional[float]:
        """
        计算对冲漂移率 - 衡量持仓偏离完美对冲的程度 (v7.40.1)

        物理含义:
            衡量当前持仓市值偏离"Dollar Neutral"的程度
            Drift% = (Net Exposure / Gross Exposure) × 100

        应用场景:
            仅用于持仓中实时监控对冲质量

        设计原则 (v7.40.1):
            - DRY: 直接调用 get_net_exposure() 和 get_gross_exposure()
            - 概念纯粹: exposure 只适用于活跃持仓,不支持平仓后复盘

        Returns:
            对冲漂移率(%) 或 None(无持仓/数据异常)

        数值解读:
            - 0%: 完美对冲 (净敞口为0)
            - 15%: 警戒区 (开始暴露于Beta风险)
            - 30%+: 危险区 (类似单边持仓)
            - 正值: 净多头敞口 (大盘涨我赚)
            - 负值: 净空头敞口 (大盘跌我赚)

        Example:
            # 持仓中监控
            drift = pair.get_hedge_drift()
            if drift and abs(drift) > 30:
                self.Debug(f"[对冲警告] {pair.pair_id} 漂移{drift:.1f}%")
        """
        # 直接调用已有方法 - DRY原则 (v7.40.1)
        net_exp = self.get_net_exposure()
        gross_exp = self.get_gross_exposure()

        # 防御性检查
        if net_exp is None or gross_exp is None or gross_exp == 0:
            return None

        # 计算漂移率 (百分比)
        return (net_exp / gross_exp) * 100


    def calculate_leg_values(self, allocated_amount: float, signal: str, data):
        """
        核心算力 - Beta对冲数学: 从分配资金计算两腿购买力,实现风险中性对冲 (v7.34.0 CRITICAL FIX)

        ========================================================================
        CRITICAL FIX (v7.34.0): 修正Beta对冲公式
        ========================================================================
        旧版错误假设: 购买力比 = β, 即 x₁/m₁ = β × x₂/m₂  ❌ 错误!
        正确对冲条件: 市值比 = β, 即 V₂ = β × V₁  ✓ 正确!

        错误原因:
        - 模型 ln(P₁) = α + β·ln(P₂) 表明 Symbol1 弹性是 Symbol2 的 β 倍
        - 要对冲 Symbol1 更大的波动,必须用 β 倍的 Symbol2 市值来抵消
        - 旧公式导致 28.1% 交易"两腿都亏损",Beta对冲从未真正生效

        影响: 所有历史回测结果失效,需重新运行回测验证修复效果
        ========================================================================

        正确数学推导:
        约束1: x₁ + x₂ = A (资金分配)
        约束2: x₂/m₂ = β × x₁/m₁ (市值比 V₂ = β × V₁)

        LONG_SPREAD (Symbol1多头 m₁=0.5, Symbol2空头 m₂=1.5):
            x₂/1.5 = β × x₁/0.5
            => x₂ = 3β × x₁
            代入约束1: x₁ + 3β×x₁ = A
            => x₁ = A/(1+3β), x₂ = 3βA/(1+3β)

            市值验证:
            V₁ = x₁/m₁ = 2A/(1+3β)
            V₂ = x₂/m₂ = 2βA/(1+3β)
            => V₂/V₁ = β ✓ 满足对冲条件

        SHORT_SPREAD (Symbol1空头 m₁=1.5, Symbol2多头 m₂=0.5):
            x₂/0.5 = β × x₁/1.5
            => x₂ = β×x₁/3
            代入约束1: x₁ + β×x₁/3 = A
            => x₁ = 3A/(3+β), x₂ = βA/(3+β)

            市值验证:
            V₁ = x₁/m₁ = 2A/(3+β)
            V₂ = x₂/m₂ = 2βA/(3+β)
            => V₂/V₁ = β ✓ 满足对冲条件

        参数:
            allocated_amount: 分配的投资资金金额 (A)
            signal: 交易信号 (LONG_SPREAD/SHORT_SPREAD)
            data: 数据切片(用于获取当前价格)

        返回:
            (value_1, value_2): Symbol1和Symbol2的目标购买市值, 计算失败返回 (None, None)

        关键设计:
            - 公式只依赖β和保证金率,保证风险中性 (市值比 V₂/V₁ = β)
            - 避免引入价格P₁,P₂,防止计算偏差
            - v7.34.0修复后预期"两腿都亏损"从28.1%降至<10%
        """
        # 获取当前价格
        prices = self.get_price(data)
        if prices is None:
            return None, None
        price_1, price_2 = prices

        # 避免除零
        if price_1 <= 0 or price_2 <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 价格异常: Symbol1={price_1}, Symbol2={price_2}")
            return None, None

        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1

        if signal == 'LONG_SPREAD':
            # Symbol1多头(m₁=0.5), Symbol2空头(m₂=1.5)
            # v7.34.0 修复: 正确公式 x₁ = A/(1+3β), x₂ = 3βA/(1+3β)
            denominator = 1 + 3 * beta  # m₁ + β × m₂ = 0.5 + β × 1.5

            x1 = allocated_amount / denominator
            x2 = allocated_amount * 3 * beta / denominator

            # 市值 = 资金 / 保证金率
            value_1 = x1 / self.margin_long    # 2A/(1+3β)
            value_2 = x2 / self.margin_short   # 2βA/(1+3β)

        else:  # SHORT_SPREAD
            # Symbol1空头(m₁=1.5), Symbol2多头(m₂=0.5)
            # v7.34.0 修复: 正确公式 x₁ = 3A/(3+β), x₂ = βA/(3+β)
            denominator = 3 + beta  # m₁ + β × m₂ = 1.5 + β × 0.5

            x1 = allocated_amount * 3 / denominator
            x2 = allocated_amount * beta / denominator

            # 市值
            value_1 = x1 / self.margin_short   # 2A/(3+β)
            value_2 = x2 / self.margin_long    # 2βA/(3+β)

        # 安全检查: 资金分配合理性
        if x1 <= 0 or x2 <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 资金分配异常: x1={x1:.2f}, x2={x2:.2f}")
            return None, None

        return value_1, value_2


    # ===== 4. 金融指标 =====

    def get_accum_return_pct(self) -> float:
        """
        获取累积收益率 (%) - 多次交易的加权平均收益

        计算公式:
            累积收益率 = (realized_pnl / realized_cost) × 100

        数学原理:
            realized_pnl   = 第1笔PnL + 第2笔PnL + ... + 第N笔PnL
            realized_cost  = 第1笔成本 + 第2笔成本 + ... + 第N笔成本
            累积收益率    = (Σ PnL / Σ Cost) × 100  (加权平均,非简单平均)

        示例:
            交易1: PnL=$500,  Cost=$10,000 → 收益率5%
            交易2: PnL=-$200, Cost=$8,000  → 收益率-2.5%
            交易3: PnL=$800,  Cost=$12,000 → 收益率6.67%

            累积收益率 = (500-200+800) / (10000+8000+12000) × 100 = 3.67%
            (注意: 不是 (5%-2.5%+6.67%)/3 = 3.06%)

        Returns:
            累积收益率百分比
            - 示例: 15.8 表示累积 15.8% 收益
            - 无交易时: 返回 0.0
            - 有交易但成本为0时: 返回 0.0 (Fail-Safe)

        调用方:
            - IndustryQuotaManager: 计算行业加权收益率
            - PerformanceAnalyzer: 生成配对历史报告
        """
        if self.realized_cost > 0:
            return (self.realized_pnl / self.realized_cost) * 100
        return 0.0


    def get_pair_position_value(self) -> float:
        """获取当前持仓市值(包括部分持仓)"""
        info = self.get_position_info()
        return info['value1'] + info['value2']


    # 3C. 时间查询

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

        return None


    def get_max_holding_days(self) -> Optional[float]:
        """
        计算理论最大持仓天数 (v7.38.2: 基于指数衰减公式)

        公式来源:
        - 均值回归路径: Z(t) = Z_entry × (0.5)^(t/half_life)
        - 求解半衰期数: n = ln(exit_threshold/entry_zscore) / ln(0.5)
        - 最大持有天数: max_days = n × half_life

        使用场景:
        - 平仓日志输出 (显示动态超时阈值)
        - 风控规则诊断 (PairHoldingTimeoutRule已内联此公式)

        Returns:
            理论最大持仓天数 或 None(数据不完整)
        """
        # 检查必需数据
        if self.entry_zscore is None or self.half_life is None:
            return None

        # 从config读取出场阈值
        exit_threshold = self.algorithm.config.pairs_trading.exit_threshold  # 0.3
        entry_zscore = abs(self.entry_zscore)  # 取绝对值,如-1.9σ → 1.9

        # 计算所需半衰期数
        import math
        n = math.log(exit_threshold / entry_zscore) / math.log(0.5)

        # 计算最大持有天数
        max_days = n * self.half_life

        return max_days


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


    def get_cooldown_days(self) -> int:
        """
        v7.28.0: NORMAL_SIGNAL从CLOSE_REASONS读取,风控规则由Rule层管理

        设计理由:
        - v7.28.0配置重构: cooldown_days迁移到risk_management
        - NORMAL_SIGNAL (MEAN_REVERSION/PAIR_BREAK): 仍从CLOSE_REASONS读取
        - 风控规则 (TIMEOUT/DRAWDOWN/CUMULATIVE_LOSS/ANOMALY): 由RiskBaseRule.config['cooldown_days']管理
        - 兼容历史版本原因通过默认值兜底

        Returns:
            冷却期天数

        冷却期分类 (v7.28.0):
            MEAN_REVERSION: 10天 (从CLOSE_REASONS读取)
            PAIR_BREAK: 10天 (从CLOSE_REASONS读取)
            TIMEOUT/DRAWDOWN/CUMULATIVE_LOSS/ANOMALY: 由Rule层管理
            其他: 10天 (默认值,兼容历史原因如NORMAL_EXIT/CLOSE)

        注意:
        - Portfolio级原因(PORTFOLIO_DRAWDOWN/ACCOUNT_BLOWUP)不参与per-pair冷却计算
        - 风控规则的冷却期由RiskBaseRule从config['cooldown_days']读取
        """
        close_reasons = self.algorithm.config.constants['close_reasons']

        # v7.28.0: 检查是否在CLOSE_REASONS中配置了cooldown_days
        if self.last_close_reason in close_reasons:
            reason_config = close_reasons[self.last_close_reason]
            if 'cooldown_days' in reason_config:
                return reason_config['cooldown_days']  # NORMAL_SIGNAL

        # 风控规则: 由RiskBaseRule.config['cooldown_days']管理
        # 返回默认值10天(仅用于向后兼容历史原因如NORMAL_EXIT/CLOSE)
        return 10


    def has_position(self) -> bool:
        """检查是否有持仓（优化后：使用 @property）"""
        return self.position_mode != PositionMode.NONE


    def has_normal_position(self) -> bool:
        """检查是否有正常持仓（优化后：使用 @property）"""
        return self.position_mode in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]


    def has_anomaly_position(self) -> bool:
        """检查是否有异常持仓"""
        return self.position_mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]


    # ===== 5. 决策与意图 =====

    def get_zscore(self, price1: float, price2: float) -> Optional[float]:
        """
        计算配对的Z-score (通用方法)

        职责: 纯计算逻辑, 不涉及数据获取

        参数:
            price1: symbol1的价格
            price2: symbol2的价格

        返回:
            Z-score值 或 None (计算失败时)

        使用场景:
            # 场景1: 信号生成时
            prices = self.get_price(data)
            if prices:
                zscore = self.get_zscore(prices[0], prices[1])

            # 场景2: 订单成交时(分析执行滑点)
            fill_zscore = self.get_zscore(ticket1.AverageFillPrice, ticket2.AverageFillPrice)

        设计演进:
            原设计: 接受data参数, 内部调用get_price()获取价格
            新设计: 接受价格参数, 职责更单一, 支持多种价格来源(实时/成交/历史)
        """
        # 价格有效性检查
        if price1 <= 0 or price2 <= 0:
            return None

        # 安全检查
        if self.residual_std <= 0:
            return None

        try:
            # 计算对数空间的残差(与贝叶斯模型一致)
            log_residual = np.log(price1) - (self.alpha_mean + self.beta_mean * np.log(price2))

            # 计算Z-score
            zscore = (log_residual - self.residual_mean) / self.residual_std

            return zscore

        except (ValueError, ZeroDivisionError, OverflowError):
            # 处理极端情况（如price<=0导致np.log失败）
            return None


    def get_signal(self, data):
        """
        获取交易信号 (cooldown检查在ExecutionManager中进行)
        一步到位的接口,内部自动计算所需信息
        """
        # 获取价格（数据获取在调用者）
        prices = self.get_price(data)
        if prices is None:
            return 'NO_DATA'

        price1, price2 = prices

        # 计算zscore（使用通用方法）
        zscore = self.get_zscore(price1, price2)
        if zscore is None:
            return 'NO_DATA'

        # 内部检查持仓
        has_position = self.has_normal_position()

        # 生成信号 (改良C方案[1.2σ, 1.8σ])
        if not has_position:
            abs_zscore = abs(zscore)

            # 检查是否在有效区间内 (改良C方案: 1.2-1.8σ)
            if self.entry_threshold_lower <= abs_zscore <= self.entry_threshold_upper:
                # Z-score高,spread偏高,做空
                if zscore > 0:
                    self.entry_zscore = zscore  # 信号触发时记录entry_zscore(而非get_open_intent()时,避免市场波动导致不一致)
                    return 'SHORT_SPREAD'
                # Z-score低,spread偏低,做多
                else:
                    self.entry_zscore = zscore  # 信号触发时记录entry_zscore(而非get_open_intent()时,避免市场波动导致不一致)
                    return 'LONG_SPREAD'
            else:
                # 区间外: |zscore| < 1.2σ (信号弱) 或 > 1.8σ (留0.5σ缓冲给止损)
                return 'WAIT'
        else:
            # 有持仓时的出场信号 (止损阈值2.3σ,配合1.8σ上限,留0.5σ缓冲,避免即开即止)
            if abs(zscore) > self.stop_loss_threshold:
                return 'PAIR_BREAK'  # 协整破裂 (Z-score超限)

            if abs(zscore) < self.exit_threshold:
                return 'CLOSE'

            return 'HOLD'


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
        """
        # 自动检测信号
        signal = self.get_signal(data)

        if signal not in ['LONG_SPREAD', 'SHORT_SPREAD']:
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
        if signal == 'LONG_SPREAD':
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

        # 三阶段Z-score记录时机见类属性注释(__init__:77-81)

        # 构建意图对象
        return OpenIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=qty1,
            qty2=qty2,
            signal=signal,
            tag=self.create_order_tag('OPEN')
        )


    def get_close_intent(self, reason='CLOSE'):
        """
        生成平仓意图（意图生成与执行分离）

        设计理念:
        - 获取当前持仓信息
        - 返回CloseIntent对象,交给OrderExecutor执行
        - 如果无持仓,返回None

        Args:
            reason: 平仓原因 (参见 config.constants['close_reasons'], 默认='CLOSE')

        Returns:
            CloseIntent对象 或 None(无持仓)

        使用示例(在ExecutionManager中):
            intent = pair.get_close_intent(reason='STOP_LOSS')
            if intent:
                tickets = order_executor.execute_close(intent)
                tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE

        设计说明:
            - reason参数会编码到tag中(便于日志追踪和统计分析)
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
            tag=self.create_order_tag('CLOSE', reason)
        )


    def get_planned_allocation_pct(self) -> float:
        """
        计算基于质量分数和行业tier的计划分配比例 (v7.32.0: tier-based max_pct)

        计算逻辑:
            planned_pct = min_pct + quality_score × (max_pct - min_pct)

        参数来源:
            - min_pct: PairsTradingConfig.min_investment_ratio (0.05)
            - max_pct: PairsTradingConfig.tier_max_investment_ratio[tier] (0.10-0.22)
            - quality_score: 配对质量分数 (0.0-1.0)

        tier影响 (v7.39.0: 默认tier1):
            - tier0 (负收益行业): max_pct=0.10 → planned_pct范围 [0.05, 0.10]
            - tier1 (默认/新行业): max_pct=0.16 → planned_pct范围 [0.05, 0.16]
            - tier4 (高回报行业): max_pct=0.22 → planned_pct范围 [0.05, 0.22]

        Returns:
            计划分配比例 (0.05-0.22之间,取决于tier和quality_score)
        """
        # v7.32.0: 从PairsTradingConfig获取tier-based max_pct
        min_pct = self.config.min_investment_ratio

        # 获取tier对应的max_pct (v7.39.0: 未设置tier时使用tier1兜底)
        tier = self.industry_quota_tier if self.industry_quota_tier else 'tier1'
        tier_max_investment_ratio = self.algorithm.config.pairs_trading.tier_max_investment_ratio
        max_pct = tier_max_investment_ratio.get(tier, tier_max_investment_ratio['tier0'])

        return min_pct + self.quality_score * (max_pct - min_pct)


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

        if action == 'CLOSE' and reason:
            # 平仓时包含reason
            return f"{self.pair_id}_{action}_{reason}_{timestamp}"
        else:
            # 开仓时或没有reason时的标准格式
            return f"{self.pair_id}_{action}_{timestamp}"


    # ===== 6. 生命周期回调 =====

    def on_position_filled(self, action: str, fill_time, tickets, reason: str = None):
        """
        生命周期回调 - 订单成交后的状态更新 (由TicketsManager外部触发)

        触发时机: TicketsManager检测到配对的所有订单都已Filled时

        职责:
        - OPEN: 记录开仓价格、数量、fill_zscore_open
        - CLOSE: 记录平仓价格、平仓原因、fill_zscore_close、更新交易统计

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            fill_time: 最后一条腿成交的时间(确保两腿都已成交)
            tickets: List[OrderTicket] 成交的订单票据列表,用于提取实际成交数量
            reason: 平仓原因 (仅CLOSE时有效, 参见 config.constants['close_reasons'])

        技术说明:
            - OrderTicket: QuantConnect SDK 订单票据类
            - OrderStatus: QuantConnect SDK 订单状态枚举 (来自 AlgorithmImports)
              包括: Filled, Canceled, Invalid, PartiallyFilled 等
            - ticket.Status: 订单当前状态 (OrderStatus 枚举值)
            - ticket.QuantityFilled: 实际成交数量
            - ticket.AverageFillPrice: 平均成交价格
        """
        if action == 'OPEN':
            self.pair_opened_time = fill_time

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 从OrderTicket提取实际成交数量和均价
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                        self.entry_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
                        self.entry_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算开仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_open = self.get_zscore(fill_price1, fill_price2)

        elif action == 'CLOSE':
            self.pair_closed_time = fill_time
            self.last_close_reason = reason  # 存储平仓原因(用于动态冷却期判断)

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 记录平仓价格(用于后续PnL计算)
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.exit_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.exit_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算平仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_close = self.get_zscore(fill_price1, fill_price2)

            # 更新交易历史统计(先更新状态)
            self._update_trade_stats()

            # 输出平仓日志(读取已更新的realized_pnl/cost)
            self._log_close_completion(reason)

            # 清零所有追踪变量
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
            self.entry_price1 = None
            self.entry_price2 = None
            self.exit_price1 = None
            self.exit_price2 = None


    def _update_trade_stats(self):
        """
        更新交易历史统计 (加权平均累计)

        在平仓时调用,计算本次交易收益并更新累计统计

        计算逻辑:
        - 本次交易PnL% = (pnl_dollars / pair_cost) * 100 (单次交易收益率)
        - 累计美元PnL += pnl_dollars (分子累加)
        - 累计保证金成本 += pair_cost (分母累加)
        - 累计收益率 = (total_pnl_dollars / total_pair_cost) * 100 (加权平均,非简单相加)

        设计理由:
        加权平均考虑不同交易的成本差异,避免简单百分比相加的数学错误

        调用时机:
        在清零追踪变量之前调用 (此时 exit_price 已记录,可计算 PnL)
        """
        # 计算本次交易的美元PnL和保证金成本
        pnl_dollars = self.get_pair_pnl()
        pair_cost = self.get_pair_cost()

        # 数据完整性检查
        if pnl_dollars is None or pair_cost is None or pair_cost <= 0:
            # 数据不完整,跳过统计更新 (理论上不应发生,因为on_position_filled时数据应完整)
            return

        # 累积美元PnL和成本(用于加权平均计算)
        self.realized_pnl += pnl_dollars  # 分子: 已实现PnL
        self.realized_cost += pair_cost   # 分母: 已实现成本

        # 更新计数统计
        self.trade_count += 1
        if pnl_dollars > 0:
            self.win_count += 1


    def _log_close_completion(self, reason: str):
        """
        输出平仓完成日志

        调用时机: on_position_filled(CLOSE) 中，在 fill_zscore_close 计算完成后

        职责:
        - 计算本次交易PnL和累计收益率
        - 格式化日志输出(包含Z-score轨迹)
        - 根据平仓原因输出不同消息("Z-score回归" vs "Z-score超限")

        Args:
            reason: 平仓原因 (v7.12.0统一: NORMAL_EXIT/DRAWDOWN/ANOMALY/PORTFOLIO_DRAWDOWN/ACCOUNT_BLOWUP)
        """
        # 计算本次交易PnL
        current_pnl = self.get_pair_pnl()
        current_cost = self.get_pair_cost()
        current_pnl_pct = (current_pnl / current_cost * 100) if (current_pnl and current_cost and current_cost > 0) else 0

        # 计算累计收益率 (直接读取已更新的realized_pnl/cost)
        total_pnl_pct = (self.realized_pnl / self.realized_cost * 100) if self.realized_cost > 0 else 0

        # 交易序号(此时 trade_count 已在 _update_trade_stats 中递增)
        trade_num = self.trade_count

        # 提取Z-score数据
        entry_z = self.entry_zscore if self.entry_zscore is not None else 0.0
        close_z = self.fill_zscore_close if self.fill_zscore_close is not None else 0.0

        # 从config.constants动态读取显示文本
        close_reasons = self.algorithm.config.constants['close_reasons']
        reason_text = close_reasons.get(reason, {}).get('display', '未知原因')

        # v7.30.11: 根据reason从config读取真实冷却期(修复BUG: 之前错误使用get_cooldown_days()返回10天)
        from datetime import timedelta

        # 风控规则的冷却期从risk_management.pair_rules读取
        risk_config = self.algorithm.config.risk_management.pair_rules
        reason_to_config = {
            'TIMEOUT': risk_config.holding_timeout.cooldown_days,              # 30天
            'DRAWDOWN': risk_config.pair_drawdown.cooldown_days,               # 180天
            'CUMULATIVE_LOSS': risk_config.pair_cumulative_loss.cooldown_days, # 360天
            'ANOMALY': risk_config.pair_anomaly.cooldown_days,                 # 999999天
        }

        # 如果是风控原因，读取配置；否则使用默认10天
        cooldown_days = reason_to_config.get(reason, self.get_cooldown_days())

        # 计算持有天数
        holding_days = self.get_pair_holding_days()

        # v7.38.2: 计算理论最大持仓天数
        max_days = self.get_max_holding_days()
        max_days_str = f"{max_days:.0f}" if max_days is not None else "N/A"

        # v7.37.1: 获取行业名称用于日志输出
        industry_names = self.algorithm.config.constants['industry_names']
        industry_name = industry_names.get(int(self.industry_code), '未知') if self.industry_code else '未知'

        self.algorithm.Debug(
            f"[平仓] {self.pair_id} | {industry_name} | {reason_text} | 持有{holding_days}天,最大{max_days_str}天 | "
            f"PnL=${current_pnl:.2f} ({current_pnl_pct:+.1f}%) | "
            f"累计{total_pnl_pct:+.1f}% | "
            f"{entry_z:+.2f}σ → {close_z:+.2f}σ | "
            f"第{trade_num}次交易 | "
            f"冷却{cooldown_days}天",
            level=0
        )

        # v7.28.2: 增强诊断 - 价格变化明细
        if self.exit_price1 and self.exit_price2 and self.entry_price1 and self.entry_price2:
            leg1_pnl = self.tracked_qty1 * (self.exit_price1 - self.entry_price1)
            leg2_pnl = self.tracked_qty2 * (self.exit_price2 - self.entry_price2)

            self.algorithm.Debug(
                f"[PnL明细] {self.pair_id} "
                f"| 开仓价=({self.entry_price1:.4f}, {self.entry_price2:.4f}) "
                f"| 平仓价=({self.exit_price1:.4f}, {self.exit_price2:.4f}) "
                f"| 数量=({self.tracked_qty1:+.0f}, {self.tracked_qty2:+.0f}) "
                f"| leg1_pnl=${leg1_pnl:+.2f} "
                f"| leg2_pnl=${leg2_pnl:+.2f}",
                level=1
            )

            # v7.39.0: 对冲漂移诊断 - 分析亏损原因(Alpha风险 vs Beta风险)
            # v7.40.1: 简化后此功能不再输出(平仓后exposure=None)
            hedge_drift = self.get_hedge_drift()
            if hedge_drift is not None:
                self.algorithm.Debug(
                    f"[对冲诊断] {self.pair_id} | 平仓时漂移={hedge_drift:+.2f}%",
                    level=1
                )
