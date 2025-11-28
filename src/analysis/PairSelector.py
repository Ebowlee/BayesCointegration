# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
from src.analysis.PairData import PairData
# endregion


class PairSelector:
    """
    配对评估和筛选器 (v8.0.10)

    核心职责:
    - 三维质量评分: half_life (25%) + mean_reversion_certainty (40%) + zero_crossing (35%)
    - 质量门槛过滤: quality_score > 0.50
    - 历史ROI过滤: cumulative_roi < -10% 的配对被排除 (预热期后生效)

    关键接口:
    - selection_procedure(): 主入口，执行完整筛选流程
    - evaluate_quality(): 计算三维质量分数
    - select_best(): 应用质量门槛和历史ROI过滤
    """

    def __init__(self, algorithm, analysis_config, module_config):
        """初始化配对选择器"""
        self.algorithm = algorithm
        self.lookback_days = analysis_config.lookback_days
        self.min_quality_threshold = module_config.min_quality_threshold
        self.quality_weights = module_config.quality_weights
        self.scoring_thresholds = module_config.scoring_thresholds
        self.historical_roi_threshold = module_config.historical_roi_threshold


    # ===== 公共方法 (Public Methods) =====

    def selection_procedure(self, modeling_results):
        """
        执行配对筛选流程

        Args:
            modeling_results: BayesianModeler输出 (symbol1, symbol2, rho_samples, spread等)

        Returns:
            List[Dict]: 筛选后的配对列表 (包含三维质量分数, 按quality_score降序)
        """
        # 步骤1: 评估配对质量（使用贝叶斯后验参数）
        scored_pairs = self.evaluate_quality(modeling_results)

        # 步骤2: 筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs


    def evaluate_quality(self, modeling_results):
        """
        评估配对质量 (三维评分: half_life 25% + MR certainty 40% + zero_crossing 35%)

        Args:
            modeling_results: BayesianModeler输出列表

        Returns:
            List[Dict]: 添加了quality_score的配对列表
        """
        scored_pairs = []

        for model_result in modeling_results:
            symbol1 = model_result['symbol1']
            symbol2 = model_result['symbol2']

            # 三维评分计算
            half_life_score, half_life_days, half_life_std = self._calculate_half_life_score(model_result)
            mean_reversion_score, snr_kappa = self._calculate_mean_reversion_certainty_score(model_result)
            zero_crossing_score, crossing_count, _ = self._calculate_zero_crossing_score(model_result)

            # 综合质量分数 (三维加权平均: 25%+40%+35%)
            quality_score = (
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['mean_reversion_certainty'] * mean_reversion_score +
                self.quality_weights['zero_crossing'] * zero_crossing_score
            )


            # 更新质量分数到model_result
            model_result['quality_score'] = quality_score
            model_result['half_life_score'] = half_life_score
            model_result['half_life'] = half_life_days
            model_result['half_life_std'] = half_life_std
            model_result['mean_reversion_score'] = mean_reversion_score
            model_result['zero_crossing_score'] = zero_crossing_score
            model_result['crossing_count'] = crossing_count

            scored_pairs.append(model_result)

        return scored_pairs


    def select_best(self, scored_pairs):
        """
        筛选最佳配对

        流程:
        1. 质量门槛过滤 (quality_score > 0.50)
        2. 历史ROI过滤 (cumulative_roi < -10%, 预热期后生效)
        3. 按质量分数降序排序
        """
        # 质量分布统计 (在质量筛选之前)
        if scored_pairs:
            # 统计各档位数量
            excellent = sum(1 for p in scored_pairs if p['quality_score'] >= 0.80)
            good = sum(1 for p in scored_pairs if 0.70 <= p['quality_score'] < 0.80)
            pass_grade = sum(1 for p in scored_pairs if 0.60 <= p['quality_score'] < 0.70)
            poor = sum(1 for p in scored_pairs if 0.30 <= p['quality_score'] < 0.60)
            very_poor = sum(1 for p in scored_pairs if p['quality_score'] < 0.30)

            # 计算极值
            max_score = max(p['quality_score'] for p in scored_pairs)
            min_score = min(p['quality_score'] for p in scored_pairs)

            self.algorithm.Debug(
                f"[质量分布] 总计{len(scored_pairs)}对 → "
                f"(≥0.80):{excellent}对 | [0.70,0.80):{good}对 | "
                f"[0.60,0.70):{pass_grade}对 | [0.30,0.60):{poor}对 | (<0.30):{very_poor}对 | "
                f"最高:{max_score:.3f} | 最低:{min_score:.3f}",
                level=1
            )

        # 零轴穿越分布统计
        if scored_pairs and any('crossing_count' in p for p in scored_pairs):
            # 统计穿越次数分档（基于评分函数设计的区间）
            crossing_excellent = sum(1 for p in scored_pairs if p.get('crossing_count', 0) >= 18)  # 平台区及以上
            crossing_good = sum(1 for p in scored_pairs if 12 <= p.get('crossing_count', 0) < 18)  # 峰值区
            crossing_moderate = sum(1 for p in scored_pairs if 6 <= p.get('crossing_count', 0) < 12)  # 上升区
            crossing_sparse = sum(1 for p in scored_pairs if 0 < p.get('crossing_count', 0) < 6)  # 低于基线
            crossing_none = sum(1 for p in scored_pairs if p.get('crossing_count', 0) == 0)  # 无穿越

            # 计算极值和平均值
            max_crossing = max((p.get('crossing_count', 0) for p in scored_pairs), default=0)
            min_crossing = min((p.get('crossing_count', 0) for p in scored_pairs), default=0)
            avg_crossing = sum(p.get('crossing_count', 0) for p in scored_pairs) / len(scored_pairs)

            self.algorithm.Debug(
                f"[零轴穿越] 总计{len(scored_pairs)}对 → "
                f"(≥18):{crossing_excellent}对 | [12,18):{crossing_good}对 | "
                f"[6,12):{crossing_moderate}对 | (0,6):{crossing_sparse}对 | "
                f"无穿越:{crossing_none}对 | "
                f"最高:{max_crossing}次 | 最低:{min_crossing}次 | 平均:{avg_crossing:.1f}次",
                level=1
            )

        # Step 1: 最低质量门槛过滤（严格大于阈值）
        min_threshold = self.min_quality_threshold  # 从config读取
        qualified_pairs = [
            p for p in scored_pairs
            if p['quality_score'] > min_threshold  # 严格大于（不包含等于）
        ]

        self.algorithm.Debug(
            f"[质量筛选] 输入{len(scored_pairs)}对 → "
            f"质量阈值>{min_threshold:.2f} → "
            f"通过{len(qualified_pairs)}对 (损失{len(scored_pairs) - len(qualified_pairs)}对)",
            level=1
        )

        # Step 1.5: 历史ROI过滤 (预热期后生效)
        if not self.algorithm.is_in_warmup_period:
            before_count = len(qualified_pairs)
            qualified_pairs = self._filter_by_historical_roi(qualified_pairs)
            filtered_count = before_count - len(qualified_pairs)
            if filtered_count > 0:
                self.algorithm.Debug(
                    f"[历史ROI] 过滤前{before_count}对 → 过滤后{len(qualified_pairs)}对 "
                    f"(排除{filtered_count}对, 阈值={self.historical_roi_threshold*100:.0f}%)",
                    level=1
                )

        # Step 2: 按质量分数排序（从高到低）
        sorted_pairs = sorted(qualified_pairs, key=lambda x: x['quality_score'], reverse=True)

        return sorted_pairs


    # ===== 私有评分方法 (Private Scoring Methods) =====

    def _calculate_half_life_score(self, model_result):
        """
        计算半衰期分数

        评分方法: 非对称高斯+软截断+指数衰减
        峰值: 8天 | 核心区间: 5-10天 | 可接受: 4-12天

        Returns:
            (score, half_life_mean, half_life_std)
        """
        try:
            # 从rho_samples按需计算rho_mean
            rho_samples = model_result.get('rho_samples')
            if rho_samples is None or len(rho_samples) == 0:
                return (0, None)

            rho_mean = float(np.mean(rho_samples))

            # rho有效性检查 (均值回归要求: ρ ∈ (0, 1))
            if rho_mean <= 0 or rho_mean >= 1:
                return (0, None)

            # 计算半衰期分布 (对每个rho_sample分别计算,避免Jensen不等式问题)
            half_life_samples = -np.log(2) / np.log(rho_samples)
            half_life = float(np.mean(half_life_samples))  # 均值
            half_life_std = float(np.std(half_life_samples))  # 标准差

            # 读取阈值
            peak_days = self.scoring_thresholds['half_life']['peak_days']       # 8天
            sigma_left = self.scoring_thresholds['half_life']['sigma_left']     # 3.5
            sigma_right = self.scoring_thresholds['half_life']['sigma_right']   # 4.5
            min_days = self.scoring_thresholds['half_life']['min_days']         # 4天
            decay_start = self.scoring_thresholds['half_life']['decay_start']   # 12天
            decay_rate = self.scoring_thresholds['half_life']['decay_rate']     # 0.6

            # 非对称高斯核心
            if half_life < peak_days:
                # 左侧: 4-8天区间 (σ=3.5保证6天≈0.90)
                score = np.exp(-((half_life - peak_days) ** 2) / (2 * sigma_left ** 2))
            else:
                # 右侧: 8-12天区间 (σ=4.5保证10天≈0.85, 12天≈0.65)
                score = np.exp(-((half_life - peak_days) ** 2) / (2 * sigma_right ** 2))

            # 软截断下界 (4天以下平滑惩罚)
            if half_life < min_days:
                lower_bound = 1 / (1 + np.exp(-3 * (half_life - min_days)))
                score *= lower_bound

            # 远端指数衰减 (12天后快速排除)
            if half_life > decay_start:
                decay = np.exp(-decay_rate * (half_life - decay_start))
                score *= decay

            return (float(score), half_life, half_life_std)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return (0, None, None)



    def _calculate_mean_reversion_certainty_score(self, model_result):
        """
        计算均值回归确定性分数

        核心思路: κ-based SNR → 逻辑斯蒂归一化
        - κ = 连续时间均值回归率
        - SNR_κ = E[κ] / Std[κ]

        Returns:
            (score, snr_kappa)
        """
        try:
            # 提取rho样本
            rho_samples = model_result.get('rho_samples')
            if rho_samples is None:
                # 降级：兼容旧版（无样本数据）
                self.algorithm.Debug("[PairSelector] rho_samples缺失,降级处理")
                return (0.0, 0.0)

            # κ转换（逐样本）
            delta_t = self.scoring_thresholds['mean_reversion_certainty']['time_delta_days']
            kappa_samples = -np.log(np.abs(rho_samples)) / delta_t

            # 精确后验统计
            kappa_mean = np.mean(kappa_samples)
            kappa_std = np.std(kappa_samples)

            # SNR计算
            if kappa_std <= 0:
                return (0.0, 0.0)
            snr_kappa = kappa_mean / kappa_std

            # 上界截断
            max_snr = self.scoring_thresholds['mean_reversion_certainty']['max_snr_kappa']
            snr_kappa = min(snr_kappa, max_snr)

            # 逻辑斯蒂评分
            a = self.scoring_thresholds['mean_reversion_certainty']['logistic_steepness']
            b = self.scoring_thresholds['mean_reversion_certainty']['logistic_midpoint']
            score = 1.0 / (1.0 + np.exp(a * (b - snr_kappa)))

            return (max(0.0, min(1.0, score)), snr_kappa)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] κ-based均值回归确定性计算失败: {e}")
            return (0.0, 0.0)


    def _calculate_zero_crossing_score(self, model_result: Dict) -> Tuple[float, int, int]:
        """
        计算零轴穿越次数评分

        评分方法: 分段线性函数，峰值12次/252天
        - 峰值: 12次 → 1.0 | 半峰: 6次 → 0.5 | 平台: 18-24次 → 0.5
        - 截断: <6次或>36次 → 0.0

        Args:
            model_result: BayesianModeler输出 (包含spread, residual_mean)

        Returns:
            (score, crossing_count, peak_value): 评分、穿越次数、峰值参考
        """
        try:
            # 读取阈值配置
            thresholds = self.scoring_thresholds['zero_crossing']
            min_cross = thresholds['min_crossings']       # 6
            peak_cross = thresholds['peak_crossings']     # 12
            half_high = thresholds['half_peak_high']      # 18
            plateau_end = thresholds['plateau_end']       # 24
            max_cross = thresholds['max_crossings']       # 36

            # 获取对数价差序列
            spread = model_result.get('spread')
            if spread is None or len(spread) < 10:
                # 数据不足,返回默认值
                return (0.0, 0, peak_cross)

            # 去均值化 (spread本身是log-space残差,需要减去均值)
            residual_mean = model_result.get('residual_mean', 0.0)
            spread_centered = spread - residual_mean

            # 计算零轴穿越次数
            signs = np.sign(spread_centered)
            # 符号变化次数 = 穿越次数
            crossing_count = int(np.sum(signs[:-1] != signs[1:]))

            # 分段线性评分
            if crossing_count < min_cross:
                # 左端硬截断: <6次 → 0.0
                score = 0.0
            elif crossing_count <= peak_cross:
                # 上升段: [6, 12] → [0.5, 1.0]
                score = 0.5 + (crossing_count - min_cross) * 0.5 / (peak_cross - min_cross)
            elif crossing_count <= half_high:
                # 下降段: (12, 18] → (1.0, 0.5]
                score = 0.5 + (half_high - crossing_count) * 0.5 / (half_high - peak_cross)
            elif crossing_count <= plateau_end:
                # 平台段: (18, 24] → 0.5
                score = 0.5
            elif crossing_count <= max_cross:
                # 衰减段: (24, 36] → (0.5, 0.0]
                score = 0.5 * (max_cross - crossing_count) / (max_cross - plateau_end)
            else:
                # 右端硬截断: >36次 → 0.0
                score = 0.0

            return (float(score), crossing_count, peak_cross)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 零轴穿越计算失败: {e}")
            return (0.0, 0, 12)


    # ===== 私有过滤方法 (Private Filter Methods) =====

    def _filter_by_historical_roi(self, pairs: List[Dict]) -> List[Dict]:
        """
        过滤历史ROI低于阈值的配对

        累积ROI < -10% 的配对被排除 (只看已平仓交易的实现PnL)
        """
        filtered = []

        for pair in pairs:
            pair_id = f"{pair['symbol1']}_{pair['symbol2']}"
            historical_pair = self.algorithm.pairs_manager.get_pair_by_id(pair_id)

            if historical_pair:
                cumulative_roi = historical_pair.get_pair_cumulative_roi()
                if cumulative_roi is not None and cumulative_roi < self.historical_roi_threshold:
                    # 跳过历史亏损严重的配对
                    self.algorithm.Debug(
                        f"[历史ROI] 排除 {pair_id}: 累积ROI={cumulative_roi*100:.2f}%",
                        level=1
                    )
                    continue

            filtered.append(pair)

        return filtered
