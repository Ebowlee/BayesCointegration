# region imports
from AlgorithmImports import *
from typing import List
from datetime import timedelta
from .AlphaState import AlphaModelState
from .PairAnalyzer import PairAnalyzer
from .SignalGenerator import SignalGenerator
# endregion


# =============================================================================
# 主Alpha模型 - BayesianCointegrationAlphaModel
# =============================================================================
class BayesianCointegrationAlphaModel(AlphaModel):
    """
    贝叶斯协整Alpha模型 - 配对交易策略的核心决策引擎
    
    该模型是整个配对交易系统的大脑，负责从原始市场数据到交易信号的
    完整处理流程。采用模块化设计，将复杂的策略分解为独立的功能模块。
    
    整体架构:
    ┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
    │DataProcessor│ --> │CointegrationAnaly│ --> │BayesianModeler  │
    └─────────────┘     └──────────────────┘     └─────────────────┘
           |                     |                         |
           v                     v                         v
      清洗后数据            协整配对列表              模型参数
           |                     |                         |
           └─────────────────────┴─────────────────────────┘
                                 |
                                 v
                        ┌──────────────────┐
                        │ SignalGenerator  │
                        └──────────────────┘
                                 |
                                 v
                            交易信号(Insights)
    
    工作流程:
    1. 月度选股触发 (OnSecuritiesChanged)
       - 接收UniverseSelection筛选的股票
       - 标记为选股日，准备完整分析
    
    2. 数据处理 (DataProcessor)
       - 下载252天历史数据
       - 数据清洗和验证
       - 输出高质量数据集
    
    3. 协整分析 (CointegrationAnalyzer)
       - 行业内配对生成
       - Engle-Granger协整检验
       - 综合质量评分和筛选
    
    4. 贝叶斯建模 (BayesianModeler)
       - MCMC参数估计
       - 动态贝叶斯更新
       - 不确定性量化
    
    5. 日常信号生成 (SignalGenerator)
       - 实时z-score计算
       - EMA平滑处理
       - 阈值触发交易信号
    
    与其他模块的交互:
    - UniverseSelection: 提供候选股票列表
    - PortfolioConstruction: 接收Insights构建持仓
    - RiskManagement: 信号可能被风险规则修改
    - Execution: 最终执行交易指令
    
    配置要求:
    必须提供完整的config字典, 包含所有子模块的参数配置。
    详见各子模块的配置参数说明。
    
    性能优化:
    - 月度批量处理，减少计算频率
    - 数据缓存避免重复下载
    - 向量化计算提升效率
    
    注意事项:
    - 选股日会进行大量计算，可能影响性能
    - 贝叶斯建模是计算瓶颈，已优化采样参数
    - 所有模块都有独立的错误处理
    """
    
    def __init__(self, algorithm, config: dict, sector_code_to_name: dict, central_pair_manager=None):
        """
        初始化Alpha模型
        
        Args:
            algorithm: QuantConnect算法实例
            config: 配置字典
            sector_code_to_name: 行业代码到名称的映射
            central_pair_manager: 中央配对管理器（可选）
        """
        super().__init__()
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name
        self.central_pair_manager = central_pair_manager
        
        # 信号持续时间配置
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 5)
        
        # 使用集中的状态管理
        self.state = AlphaModelState()
        
        # 创建配对分析器（整合了数据处理、协整分析和贝叶斯建模）
        self.pair_analyzer = PairAnalyzer(
            self.algorithm, 
            self.config, 
            self.sector_code_to_name,
            self.state
        )
        
        # 创建信号生成器
        self.signal_generator = SignalGenerator(self.algorithm, self.config, self.state)
        
        # 初始化不需要输出
    
    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        处理证券变更事件
        步骤1: 解析选股结果
        """
        self.state.update_control_state('is_selection_day', True)
        
        # 添加新股票（避免重复）
        current_symbols = self.state.control['symbols']
        current_symbols.extend([
            s.Symbol for s in changes.AddedSecurities 
            if s.Symbol and s.Symbol not in current_symbols
        ])
        
        # 移除旧股票（保持列表更新）
        self.state.update_control_state('symbols', [
            s for s in current_symbols 
            if s not in [r.Symbol for r in changes.RemovedSecurities]
        ])
        
    
    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        """
        主更新方法
        """
        symbols = self.state.control['symbols']
        if not symbols or len(symbols) < 2:
            return []
        
        insights = []  # 初始化insights列表
        
        if self.state.control['is_selection_day']:
            # 使用配对分析器执行完整的配对分析流程
            analysis_result = self.pair_analyzer.analyze(symbols)
            
            # 清理过期配对中的持仓资产
            self._liquidate_obsolete_positions(analysis_result['modeled_pairs'])
            
            # 保存建模结果
            if analysis_result['modeled_pairs']:
                # 更新配对记录：保存旧配对，更新新配对
                self.state.update_persistent_data('previous_modeled_pairs', 
                                                 self.state.persistent.get('modeled_pairs', []))
                self.state.update_persistent_data('modeled_pairs', analysis_result['modeled_pairs'])
                self.algorithm.Debug(
                    f"[AlphaModel] 配对分析完成: {len(analysis_result['modeled_pairs'])}个配对"
                )
                
                # 提交配对到CPM
                if self.central_pair_manager:
                    try:
                        # 准备提交数据，保留原始顺序信息
                        pairs_data = []
                        for pair in analysis_result['modeled_pairs']:
                            pairs_data.append({
                                'symbol1': pair['symbol1'].Value if hasattr(pair['symbol1'], 'Value') else str(pair['symbol1']),
                                'symbol2': pair['symbol2'].Value if hasattr(pair['symbol2'], 'Value') else str(pair['symbol2']),
                                'beta': pair.get('beta', 1.0),
                                'alpha': pair.get('alpha', 0.0),
                                'quality_score': pair.get('quality_score', 0.5)
                            })
                        
                        # 提交给CPM
                        if self.central_pair_manager.submit_modeled_pairs(pairs_data):
                            self.algorithm.Debug(f"[AlphaModel] 成功提交{len(pairs_data)}个配对到CPM")
                        else:
                            self.algorithm.Debug("[AlphaModel] 警告：提交配对到CPM失败")
                    except Exception as e:
                        self.algorithm.Debug(f"[AlphaModel] CPM提交异常：{str(e)}")
            
            # 重置选股标志
            self.state.update_control_state('is_selection_day', False)
            
            # 清理临时数据，释放内存
            self.state.clear_temporary()
        
        # 日常信号生成 - 基于实时价格生成交易信号
        modeled_pairs = self.state.persistent['modeled_pairs']
        if modeled_pairs:
            self.algorithm.Debug(f"[AlphaModel] 生成信号: 跟踪{len(modeled_pairs)}对配对")
            daily_insights = self.signal_generator.generate_signals(modeled_pairs, data)
            if daily_insights:
                insights.extend(daily_insights)
                self.algorithm.Debug(f"[AlphaModel] 生成{len(daily_insights)}个日常Insights")
        
        # 返回所有insights
        if insights:
            self.algorithm.Debug(f"[AlphaModel] 总计生成{len(insights)}个Insights")
        return insights
    
    def _liquidate_obsolete_positions(self, new_modeled_pairs: List[Dict]):
        """
        清理过期配对中的持仓资产
        
        Args:
            new_modeled_pairs: 新的配对列表
        """
        # 获取旧配对
        old_pairs = self.state.persistent.get('previous_modeled_pairs', [])
        if not old_pairs:
            return
        
        # 构建新配对集合（使用排序的元组作为key，确保顺序一致）
        new_pair_set = set()
        for pair in new_modeled_pairs:
            pair_key = tuple(sorted([pair['symbol1'], pair['symbol2']]))
            new_pair_set.add(pair_key)
        
        # 找出过期的配对并清理其资产
        for old_pair in old_pairs:
            pair_key = tuple(sorted([old_pair['symbol1'], old_pair['symbol2']]))
            
            # 如果这个配对不在新配对中，说明过期了
            if pair_key not in new_pair_set:
                # 清理该配对的两个资产（如果有持仓）
                symbol1, symbol2 = old_pair['symbol1'], old_pair['symbol2']
                
                if self.algorithm.Portfolio[symbol1].Invested:
                    self.algorithm.Liquidate(symbol1)
                    self.algorithm.Debug(f"[AlphaModel] 清理过期配对资产: {symbol1.Value} (from {symbol1.Value}&{symbol2.Value})")
                
                if self.algorithm.Portfolio[symbol2].Invested:
                    self.algorithm.Liquidate(symbol2)
                    self.algorithm.Debug(f"[AlphaModel] 清理过期配对资产: {symbol2.Value} (from {symbol1.Value}&{symbol2.Value})")