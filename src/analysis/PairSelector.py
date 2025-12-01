# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, List, Tuple, Optional
# endregion


class PairSelector:
    """
    配对筛选器 (v8.8.0 阈值筛选模式)

    核心职责:
    - 四维度阈值筛选: CV BETA、半衰期、Hurst指数、零轴穿越
    - ROI缩放: 历史表现影响筛选优先级

    设计变更 (v8.8.0):
    - 移除: 评分系统 (quality_weights, scoring_thresholds)
    - 新增: 阈值筛选 (pass/fail逻辑)
    - 效果: 简化逻辑，消除评分噪音

    关键接口:
    - selection_procedure(): 主入口，执行完整筛选流程
    """

    def __init__(self, algorithm, analysis_config, module_config):
        """初始化配对选择器"""
        self.algorithm = algorithm
        self.config = module_config
        self.roi_scaling_enabled = module_config.roi_scaling_enabled


    # ===== 公共方法 (Public Methods) =====

    def selection_procedure(self, modeling_results: List[Dict]) -> List[Dict]:
        """
        执行配对筛选流程 (v8.8.0: 阈值筛选模式)

        四重过滤，全部通过才保留:
        1. CV BETA 稳定性
        2. 半衰期范围
        3. Hurst 指数 (均值回归特性)
        4. 零轴穿越次数 (活跃度)

        Args:
            modeling_results: BayesianModeler输出列表

        Returns:
            List[Dict]: 通过所有筛选的配对列表
        """
        filtered = []

        for result in modeling_results:
            # 维度1: CV BETA 稳定性
            if not self._filter_by_cv_beta(result):
                continue

            # 维度2: 半衰期
            if not self._filter_by_half_life(result):
                continue

            # 维度3: Hurst 指数
            if not self._filter_by_hurst(result):
                continue

            # 维度4: 零轴穿越
            if not self._filter_by_zero_crossing(result):
                continue

            filtered.append(result)

        # ROI缩放 (如启用)
        if self.roi_scaling_enabled and filtered:
            filtered = self._apply_roi_scaling(filtered)

        # 按ROI缩放因子排序 (高者优先)
        filtered.sort(key=lambda x: x.get('scale_factor', 1.0), reverse=True)

        return filtered


    # ===== 维度1: CV BETA 稳定性 =====

    def _filter_by_cv_beta(self, model_result: Dict) -> bool:
        """
        CV BETA 稳定性筛选

        公式: CV = beta_std / |beta_mean|
        含义: β估计的相对不确定性

        Returns:
            True: 通过筛选
            False: 被剔除
        """
        beta_mean = model_result.get('beta_mean')
        beta_std = model_result.get('beta_std')

        # 边界处理: 数据缺失
        if beta_mean is None or beta_std is None:
            return False

        # 边界处理: |beta_mean| 太小时 CV 会爆炸
        if abs(beta_mean) < self.config.min_abs_beta:
            return False

        cv_beta = beta_std / abs(beta_mean)
        passed = cv_beta <= self.config.cv_beta_threshold

        # 记录筛选指标
        model_result['cv_beta'] = cv_beta

        return passed


    # ===== 维度2: 半衰期 =====

    def _filter_by_half_life(self, model_result: Dict) -> bool:
        """
        半衰期筛选

        公式: half_life = -ln(2) / ln(|rho_mean|)
        含义: 残差回归到一半所需天数

        Returns:
            True: 通过筛选
            False: 被剔除
        """
        # 从rho_samples计算half_life
        rho_samples = model_result.get('rho_samples')
        if rho_samples is None or len(rho_samples) == 0:
            return False

        rho_mean = float(np.mean(rho_samples))

        # rho有效性检查 (均值回归要求: ρ ∈ (0, 1))
        if rho_mean <= 0 or rho_mean >= 1:
            return False

        half_life = -np.log(2) / np.log(rho_mean)

        # 阈值筛选
        passed = self.config.half_life_min <= half_life <= self.config.half_life_max

        # 记录筛选指标
        model_result['half_life'] = half_life

        return passed


    # ===== 维度3: Hurst 指数 =====

    def _filter_by_hurst(self, model_result: Dict) -> bool:
        """
        Hurst 指数筛选

        含义:
            H < 0.5 → 均值回归 (好)
            H ≈ 0.5 → 随机游走 (差)
            H > 0.5 → 趋势持续 (差)

        Returns:
            True: 通过筛选
            False: 被剔除
        """
        spread = model_result.get('spread')
        if spread is None or len(spread) < 50:
            return False

        hurst = self._compute_hurst_exponent(spread)
        if hurst is None:
            return False

        passed = hurst < self.config.hurst_threshold

        # 记录筛选指标
        model_result['hurst'] = hurst

        return passed


    def _compute_hurst_exponent(self, series: np.ndarray) -> Optional[float]:
        """
        使用 R/S 分析计算 Hurst 指数

        算法:
        1. 将序列分成 k 个子序列
        2. 计算每个子序列的 R/S 统计量
        3. 对数回归: log(R/S) = H * log(n) + c

        Returns:
            Hurst指数 (float) 或 None (计算失败)
        """
        try:
            n = len(series)
            max_k = int(n / 4)

            if max_k < 10:
                return None

            rs_values = []
            n_values = []

            for k in range(10, max_k):
                subseries = np.array_split(series, k)
                rs_list = []

                for sub in subseries:
                    if len(sub) < 2:
                        continue
                    # 去均值
                    mean_adj = sub - np.mean(sub)
                    # 累积偏差
                    cumsum = np.cumsum(mean_adj)
                    # R = max - min
                    R = np.max(cumsum) - np.min(cumsum)
                    # S = 标准差
                    S = np.std(sub, ddof=1)
                    if S > 0:
                        rs_list.append(R / S)

                if rs_list:
                    rs_values.append(np.mean(rs_list))
                    n_values.append(n / k)

            if len(rs_values) < 2:
                return None

            # 对数回归: log(R/S) = H * log(n) + c
            log_n = np.log(n_values)
            log_rs = np.log(rs_values)
            slope, _ = np.polyfit(log_n, log_rs, 1)

            return float(slope)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] Hurst计算失败: {e}")
            return None


    # ===== 维度4: 零轴穿越 =====

    def _filter_by_zero_crossing(self, model_result: Dict) -> bool:
        """
        零轴穿越次数筛选

        计算: 252天内 spread 穿越均值的次数
        含义: 穿越越多 → 均值回归机会越多

        Returns:
            True: 通过筛选
            False: 被剔除
        """
        spread = model_result.get('spread')
        if spread is None or len(spread) < 10:
            return False

        crossing_count = self._compute_zero_crossing_count(spread)

        passed = self.config.zero_crossing_min <= crossing_count <= self.config.zero_crossing_max

        # 记录筛选指标
        model_result['crossing_count'] = crossing_count

        return passed


    def _compute_zero_crossing_count(self, spread: np.ndarray) -> int:
        """计算 spread 穿越均值的次数"""
        mean_spread = np.mean(spread)
        centered = spread - mean_spread
        # 符号变化 = 穿越
        signs = np.sign(centered)
        crossings = np.sum(signs[:-1] != signs[1:])
        return int(crossings)


    # ===== ROI 缩放 (历史表现反馈) =====

    def _apply_roi_scaling(self, pairs: List[Dict]) -> List[Dict]:
        """
        应用ROI缩放因子

        公式: scale_factor = 1 + tanh(ROI)

        特性:
            - ROI=0 (无历史): scale=1, 不影响排序
            - ROI>0: scale>1, 优先排序
            - ROI<0: scale<1, 降低优先级
            - 有界: scale ∈ (0, 2)

        Args:
            pairs: 通过筛选的配对列表

        Returns:
            添加 scale_factor 字段的配对列表
        """
        for pair in pairs:
            # v8.1.6: 修复pair_id格式 (tuple而非string)
            pair_id = (pair['symbol1'].Value, pair['symbol2'].Value)
            historical_pair = self.algorithm.pairs_manager.get_pair_by_id(pair_id)

            # 默认: 无历史时 scale=1.0
            scale_factor = 1.0
            roi = None

            if historical_pair:
                roi = historical_pair.get_pair_roi()
                if roi is not None:
                    scale_factor = 1.0 + np.tanh(roi)

            pair['roi'] = roi
            pair['scale_factor'] = scale_factor

        return pairs
