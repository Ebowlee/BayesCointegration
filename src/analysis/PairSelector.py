# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, List, Tuple, Optional
# endregion


class PairSelector:
    """
    配对筛选器 (v8.9.1 三维度阈值筛选)

    核心职责:
    - 三维度阈值筛选: CV BETA、半衰期、零轴穿越
    - 漏斗日志: 展示每个维度的筛选效果

    设计变更:
    - v8.9.0: 删除 ROI 缩放逻辑，新增漏斗日志
    - v8.9.1: 删除 Hurst 维度 (60天spread数据不足以稳健计算)

    关键接口:
    - selection_procedure(): 主入口，执行完整筛选流程
    """

    def __init__(self, algorithm, analysis_config, module_config):
        """初始化配对选择器"""
        self.algorithm = algorithm
        self.config = module_config


    # ===== 公共方法 (Public Methods) =====

    def selection_procedure(self, modeling_results: List[Dict]) -> List[Dict]:
        """
        执行配对筛选流程 (v8.9.1: 三维度阈值筛选 + 漏斗日志)

        三重过滤，全部通过才保留:
        1. CV BETA 稳定性
        2. 半衰期范围
        3. 零轴穿越次数 (活跃度)

        Args:
            modeling_results: BayesianModeler输出列表

        Returns:
            List[Dict]: 通过所有筛选的配对列表
        """
        # 初始化计数器
        input_count = len(modeling_results)
        cv_beta_rejected = 0
        half_life_rejected = 0
        zero_crossing_rejected = 0

        filtered = []

        for result in modeling_results:
            # 维度1: CV BETA 稳定性
            if not self._filter_by_cv_beta(result):
                cv_beta_rejected += 1
                continue

            # 维度2: 半衰期
            if not self._filter_by_half_life(result):
                half_life_rejected += 1
                continue

            # 维度3: 零轴穿越
            if not self._filter_by_zero_crossing(result):
                zero_crossing_rejected += 1
                continue

            filtered.append(result)

        # 漏斗日志
        output_count = len(filtered)
        self.algorithm.Debug(
            f"[PairSelector] 输入 {input_count} → "
            f"CV_BETA (-{cv_beta_rejected}) → "
            f"Half_life (-{half_life_rejected}) → "
            f"ZeroCrossing (-{zero_crossing_rejected}) → "
            f"输出 {output_count}"
        )

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


    # ===== 维度3: 零轴穿越 =====

    def _filter_by_zero_crossing(self, model_result: Dict) -> bool:
        """
        零轴穿越次数筛选

        计算: spread 穿越均值的次数
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

        # v8.14.0: 添加调试日志
        pair_id = (model_result.get('symbol1'), model_result.get('symbol2'))
        status = "通过" if passed else "剔除"
        self.algorithm.Debug(
            f"[ZeroCrossing] {pair_id} | crossing={crossing_count} | "
            f"阈值=[{self.config.zero_crossing_min}, {self.config.zero_crossing_max}] | {status}",
            level=2
        )

        return passed


    def _compute_zero_crossing_count(self, spread: np.ndarray) -> int:
        """计算 spread 穿越均值的次数"""
        mean_spread = np.mean(spread)
        centered = spread - mean_spread
        # 符号变化 = 穿越
        signs = np.sign(centered)
        crossings = np.sum(signs[:-1] != signs[1:])
        return int(crossings)
