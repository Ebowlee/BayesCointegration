# region imports
from AlgorithmImports import *
from typing import List, Dict
from datetime import timedelta
from collections import defaultdict
import numpy as np
from .AlphaState import AlphaModelState
from .DataProcessor import DataProcessor
from .CointegrationAnalyzer import CointegrationAnalyzer
from .BayesianModeler import BayesianModeler
from .SignalGenerator import SignalGenerator
# endregion


class BayesianCointegrationAlphaModel(AlphaModel):
    """贝叶斯协整Alpha模型 - 配对交易策略的核心决策引擎"""

    def __init__(self, algorithm, config: dict, sector_code_to_name: dict):
        super().__init__()
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name

        # 信号持续时间配置
        self.flat_signal_duration_days = config['flat_signal_duration_days']

        # 使用集中的状态管理
        self.state = AlphaModelState()

        # 配对质量评分权重（策略参数）
        self.quality_weights = config['quality_weights']

        # 配对筛选参数（策略规则）
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']

        # 直接创建三个独立的分析模块
        self.data_processor = DataProcessor(self.algorithm, self.config)
        self.cointegration_analyzer = CointegrationAnalyzer(self.algorithm, self.config)
        self.bayesian_modeler = BayesianModeler(self.algorithm, self.config, self.state)

        # 创建信号生成器
        self.signal_generator = SignalGenerator(self.algorithm, self.config, self.state)

    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """处理证券变更事件"""
        self.state.update_control_state('is_selection_day', True)

        # 添加新股票
        current_symbols = self.state.control['symbols']
        current_symbols.extend([
            s.Symbol for s in changes.AddedSecurities
            if s.Symbol and s.Symbol not in current_symbols
        ])

        # 移除旧股票
        self.state.update_control_state('symbols', [
            s for s in current_symbols
            if s not in [r.Symbol for r in changes.RemovedSecurities]
        ])

    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        """主更新方法"""
        symbols = self.state.control['symbols']
        if not symbols or len(symbols) < 2:
            return []

        insights = []

        if self.state.control['is_selection_day']:
            # 步骤1: 数据处理 - 获取和清洗历史数据
            data_result = self.data_processor.process(symbols)
            clean_data = data_result['clean_data']
            valid_symbols = data_result['valid_symbols']

            # 保存到状态供其他模块使用
            self.state.update_temporary_data('clean_data', clean_data)
            self.state.update_temporary_data('valid_symbols', valid_symbols)

            # 检查有效股票数量
            if len(valid_symbols) < 2:
                self.algorithm.Debug("[AlphaModel] 数据处理后有效股票不足，跳过配对分析")
                self.state.update_control_state('is_selection_day', False)
                self.state.clear_temporary()
                return []

            # 步骤2: 协整检验 - 识别统计上稳定的配对
            cointegration_result = self.cointegration_analyzer.find_cointegrated_pairs(
                valid_symbols,
                clean_data,
                self.sector_code_to_name
            )

            raw_pairs = cointegration_result['raw_pairs']
            if not raw_pairs:
                self.algorithm.Debug("[AlphaModel] 未发现协整配对")
                self.state.update_control_state('is_selection_day', False)
                self.state.clear_temporary()
                return []

            self.algorithm.Debug(f"[AlphaModel] 步骤2完成: 发现{len(raw_pairs)}个协整对")

            # 步骤3: 质量评估 - 计算每个配对的综合质量分数
            scored_pairs = self._evaluate_pair_quality(raw_pairs)
            self.algorithm.Debug(f"[AlphaModel] 步骤3完成: 质量评分完成")

            # 步骤4: 配对筛选 - 根据策略规则选择最佳配对
            selected_pairs = self._select_best_pairs(scored_pairs)
            self.state.update_temporary_data('cointegrated_pairs', selected_pairs)

            if not selected_pairs:
                self.algorithm.Debug("[AlphaModel] 筛选后无合格配对")
                self.state.update_control_state('is_selection_day', False)
                self.state.clear_temporary()
                return []

            self.algorithm.Debug(f"[AlphaModel] 步骤4完成: 筛选出{len(selected_pairs)}个最佳配对")

            # 步骤5: 贝叶斯建模 - 估计交易参数
            modeling_result = self.bayesian_modeler.model_pairs(
                selected_pairs,
                clean_data
            )

            # 保存建模结果
            modeled_pairs = modeling_result['modeled_pairs']
            if modeled_pairs:
                self.state.update_persistent_data('previous_modeled_pairs',
                                                 self.state.persistent.get('modeled_pairs', []))
                self.state.update_persistent_data('modeled_pairs', modeled_pairs)
                self.algorithm.Debug(
                    f"[AlphaModel] 步骤5完成: {len(modeled_pairs)}个配对成功建模"
                )

            # 重置选股标志
            self.state.update_control_state('is_selection_day', False)

            # 清理临时数据
            self.state.clear_temporary()

        # 日常信号生成
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

    # ==================== 策略决策方法 ====================

    def _evaluate_pair_quality(self, pairs: List[Dict]) -> List[Dict]:
        """步骤3: 评估配对质量 - 策略核心决策逻辑"""
        for pair in pairs:
            # 计算各维度分数
            statistical_score = 1 - pair['pvalue']  # p值越小越好
            correlation_score = pair['correlation']  # 相关性越高越好
            liquidity_score = pair.get('liquidity_match', 0.5)  # 流动性匹配度

            # 加权综合计算质量分数
            quality_score = (
                self.quality_weights['statistical'] * statistical_score +
                self.quality_weights['correlation'] * correlation_score +
                self.quality_weights['liquidity'] * liquidity_score
            )

            pair['quality_score'] = float(quality_score)

        # 输出质量分数统计
        scores = [p['quality_score'] for p in pairs]
        if scores:
            self.algorithm.Debug(
                f"[AlphaModel] 质量分数分布: 平均{np.mean(scores):.3f}, "
                f"最高{max(scores):.3f}, 最低{min(scores):.3f}"
            )

        return pairs

    def _select_best_pairs(self, scored_pairs: List[Dict]) -> List[Dict]:
        """步骤4: 选择最佳配对 - 应用策略筛选规则"""
        # 按质量分数排序
        sorted_pairs = sorted(scored_pairs, key=lambda x: x['quality_score'], reverse=True)

        # 应用筛选规则
        symbol_count = defaultdict(int)
        selected_pairs = []

        for pair in sorted_pairs:
            # 检查是否达到最大配对数
            if len(selected_pairs) >= self.max_pairs:
                break

            symbol1, symbol2 = pair['symbol1'], pair['symbol2']

            # 检查每个股票的出现次数限制
            if (symbol_count[symbol1] < self.max_symbol_repeats and
                symbol_count[symbol2] < self.max_symbol_repeats):
                selected_pairs.append(pair)
                symbol_count[symbol1] += 1
                symbol_count[symbol2] += 1

        # 输出筛选结果
        if selected_pairs:
            # 按行业分组输出
            sector_pairs = defaultdict(list)
            for pair in selected_pairs:
                sector = pair.get('sector', 'Unknown')
                symbol1 = pair['symbol1'].Value
                symbol2 = pair['symbol2'].Value
                quality_score = pair['quality_score']
                sector_pairs[sector].append(f"({symbol1},{symbol2})/{quality_score:.3f}")

            for sector, pairs in sorted(sector_pairs.items()):
                pairs_str = ", ".join(pairs)
                self.algorithm.Debug(f"[AlphaModel] {sector}: {pairs_str}")

        return selected_pairs