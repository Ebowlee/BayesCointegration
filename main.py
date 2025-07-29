# region imports
from AlgorithmImports import *
from src.UniverseSelection import MyUniverseSelectionModel
from System import Action
from QuantConnect.Data.Fundamental import MorningstarSectorCode
from src.AlphaModel import BayesianCointegrationAlphaModel
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
# from QuantConnect.Algorithm.Framework.Risk import MaximumDrawdownPercentPortfolio, MaximumSectorExposureRiskManagementModel
# from src.RiskManagement import BayesianCointegrationRiskManagementModel
# endregion

class PairInfo:
    """简化版配对信息类"""
    
    def __init__(self, symbol1: Symbol, symbol2: Symbol):
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.is_active = False


class PairLedger:
    """简化版配对记账簿"""
    
    def __init__(self):
        # 所有配对信息 {(symbol1, symbol2): PairInfo}
        self.all_pairs = {}
        
        # 活跃配对集合（有持仓的配对）
        self.active_pairs = set()
        
        # 配对双向映射 {Symbol: Symbol} - 保留原有功能
        self.symbol_map = {}
    
    def update_pairs_from_selection(self, new_pairs: List[Tuple[Symbol, Symbol]]):
        """
        处理新一轮选股结果
        
        Args:
            new_pairs: 新选出的配对列表，每个元素为 (symbol1, symbol2)
        """
        # 记录本轮选股中的配对
        current_round_pairs = set()
        
        # 处理新配对
        for symbol1, symbol2 in new_pairs:
            # 确保symbol1 < symbol2（按字符串排序）以避免重复
            if symbol1.Value > symbol2.Value:
                symbol1, symbol2 = symbol2, symbol1
            
            pair_key = (symbol1, symbol2)
            current_round_pairs.add(pair_key)
            
            if pair_key not in self.all_pairs:
                # 添加新配对
                self.all_pairs[pair_key] = PairInfo(symbol1, symbol2)
        
        # 清理休眠且不在本轮选股中的配对
        pairs_to_remove = []
        for pair_key, pair_info in self.all_pairs.items():
            if not pair_info.is_active and pair_key not in current_round_pairs:
                pairs_to_remove.append(pair_key)
        
        for pair_key in pairs_to_remove:
            del self.all_pairs[pair_key]
        
        # 更新symbol映射
        self.symbol_map.clear()
        for (symbol1, symbol2) in self.all_pairs.keys():
            self.symbol_map[symbol1] = symbol2
            self.symbol_map[symbol2] = symbol1
    
    def set_pair_status(self, symbol1: Symbol, symbol2: Symbol, is_active: bool):
        """设置配对状态"""
        pair_key = self._get_pair_key(symbol1, symbol2)
        if pair_key in self.all_pairs:
            self.all_pairs[pair_key].is_active = is_active
            if is_active:
                self.active_pairs.add(pair_key)
            else:
                self.active_pairs.discard(pair_key)
    
    def get_active_pairs_count(self) -> int:
        """返回活跃配对数量"""
        return len(self.active_pairs)
    
    def get_paired_symbol(self, symbol: Symbol) -> Symbol:
        """获取配对股票（保留原有功能）"""
        return self.symbol_map.get(symbol)
    
    def _get_pair_key(self, symbol1: Symbol, symbol2: Symbol) -> tuple:
        """获取标准化的配对键"""
        if symbol1.Value > symbol2.Value:
            return (symbol2, symbol1)
        return (symbol1, symbol2)
    


class StrategyConfig:
    """
    策略参数统一配置类
    """
    def __init__(self):
        # Main 配置 - main.py中的硬编码参数
        self.main = {
            'start_date': (2024, 6, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,
            'schedule_frequency': 'MonthStart',
            'schedule_time': (9, 10),
            'portfolio_max_drawdown': 0.1,
            'portfolio_max_sector_exposure': 0.3
        }
        
        # UniverseSelection 配置
        self.universe_selection = {
            'max_stocks_per_sector': 30,
            'min_price': 15,
            'min_volume': 2.5e8,
            'min_days_since_ipo': 1095,
            'max_pe': 50,
            'min_roe': 0.05,
            'max_debt_ratio': 0.6,
            'max_leverage_ratio': 5
        }
        
        # AlphaModel 配置
        self.alpha_model = {
            'pvalue_threshold': 0.025,
            'correlation_threshold': 0.5,
            'max_symbol_repeats': 1,
            'max_pairs': 4,
            'lookback_period': 252,
            'mcmc_warmup_samples': 1000,
            'mcmc_posterior_samples': 1000,
            'mcmc_chains': 2,
            'entry_threshold': 1.65,
            'exit_threshold': 0.3,
            'upper_limit': 3.0,
            'lower_limit': -3.0,
            'max_annual_volatility': 0.45,
            'volatility_window_days': 63,
            'selection_interval_days': 30,  # 选股间隔天数，用于动态贝叶斯更新
            'dynamic_update_enabled': True,  # 是否启用动态贝叶斯更新
            'min_beta_threshold': 0.2,       # Beta最小阈值，过滤极小beta的协整对
            'max_beta_threshold': 3.0,       # Beta最大阈值，过滤极大beta的协整对
            'flat_signal_duration_days': 1,  # 平仓信号有效期（天）
            'entry_signal_duration_days': 2, # 建仓信号有效期（天）
            'min_data_completeness_ratio': 0.98,  # 数据完整性最低要求(98%)
            'prior_coverage_ratio': 0.95,          # 先验分布覆盖比例(95%)
            'min_mcmc_samples': 500,               # MCMC采样最少样本数
        }
        
        # PortfolioConstruction 配置
        self.portfolio_construction = {
            'margin_rate': 1.0,
            'pair_reentry_cooldown_days': 7,
            'max_pairs': 8,  # 最大持仓配对数
            'cash_buffer': 0.05  # 5%现金缓冲
        }
        
        # RiskManagement 配置
        self.risk_management = {
            'max_drawdown_percent': 0.10
        }
        
        # 行业映射配置 (共享给UniverseSelection和AlphaModel使用)
        self.sector_code_to_name = {
            MorningstarSectorCode.Technology: "Technology",
            MorningstarSectorCode.Healthcare: "Healthcare",
            MorningstarSectorCode.Energy: "Energy",
            MorningstarSectorCode.ConsumerDefensive: "ConsumerDefensive",
            MorningstarSectorCode.ConsumerCyclical: "ConsumerCyclical",
            MorningstarSectorCode.CommunicationServices: "CommunicationServices",
            MorningstarSectorCode.Industrials: "Industrials",
            MorningstarSectorCode.Utilities: "Utilities"
        }
        self.sector_name_to_code = {v: k for k, v in self.sector_code_to_name.items()}



class BayesianCointegrationStrategy(QCAlgorithm):
    """
    贝叶斯动态协整交易策略  
    
    该策略通过以下步骤运作：
    1. 选股：基于协整关系筛选资产对
    2. 信号生成：利用贝叶斯方法动态更新模型并生成交易信号
    3. 组合构建：根据信号构建投资组合
    4. 风险管理：动态调整持仓和风险暴露
    5. 交易执行：优化订单执行
    """
    
    def Initialize(self):
        """
        初始化算法、设置参数、注册事件处理程序
        """
        # 创建统一配置
        self.config = StrategyConfig()
        
        # 创建配对记账簿实例
        self.pair_ledger = PairLedger()
        
        # 设置回测时间段和初始资金
        self.SetStartDate(*self.config.main['start_date'])
        self.SetEndDate(*self.config.main['end_date'])
        self.SetCash(self.config.main['cash'])
        
        # 设置分辨率和账户类型
        self.UniverseSettings.Resolution = self.config.main['resolution']
        self.SetBrokerageModel(self.config.main['brokerage_name'], self.config.main['account_type'])
        

        # 设置UniverseSelection模块
        self.universe_selector = MyUniverseSelectionModel(self, self.config.universe_selection, self.config.sector_code_to_name, self.config.sector_name_to_code)
        self.SetUniverseSelection(self.universe_selector)
        # 设置选股调度
        schedule_frequency = self.config.main['schedule_frequency']
        schedule_time = self.config.main['schedule_time']
        date_rule = getattr(self.DateRules, schedule_frequency)()
        time_rule = self.TimeRules.At(*schedule_time)
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))

        # 设置Alpha模块
        self.SetAlpha(BayesianCointegrationAlphaModel(self, self.config.alpha_model, self.pair_ledger, self.config.sector_code_to_name))

        # 设置PortfolioConstruction模块
        self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(self, self.config.portfolio_construction, self.pair_ledger))

        # # 设置RiskManagement模块
        # ## 组合层面风控
        # self.AddRiskManagement(MaximumDrawdownPercentPortfolio(self.config.main['portfolio_max_drawdown']))
        # self.AddRiskManagement(MaximumSectorExposureRiskManagementModel(self.config.main['portfolio_max_sector_exposure']))
        # ## 资产层面风控
        # self.risk_manager = BayesianCointegrationRiskManagementModel(self, self.config.risk_management, self.pair_ledger)
        # self.AddRiskManagement(self.risk_manager)

        # # # # 设置Execution模块
        # # # self.SetExecution(MyExecutionModel(self))
        
        # # # 记录初始化完成
        # # self.Debug(f"[Initialize] 完成, 起始日期: {self.StartDate}")
    

       
