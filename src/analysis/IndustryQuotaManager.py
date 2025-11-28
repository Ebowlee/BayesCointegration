# region imports
from AlgorithmImports import *
from typing import Dict
from collections import defaultdict
import random
import numpy as np
# endregion


class IndustryQuotaManager:
    """
    行业配额管理器 - 控制每个行业的配对数量上限

    核心流程:
        1. calculate_quotas(): 根据行业历史表现计算每个行业的配额
           - 预热期(前90天): 返回空字典，使用默认配额
           - 正常期: 基于composite_score的指数权重分配全局配额

        2. apply_quotas(): 应用配额到协整结果
           - 按行业分组 → 确定性随机打乱 → 按配额+单股限制筛选
    """


    def __init__(self, algorithm, config: 'IndustryQuotaManagerConfig'):
        """
        初始化行业配额管理器 (v8.0.25: warmup检查完全内部化)

        Args:
            algorithm: QCAlgorithm实例
            config: IndustryQuotaManagerConfig实例
        """
        self.algorithm = algorithm
        self.total_quota = config.total_quota
        self.exp_scale_factor = config.exp_scale_factor
        self.weight_offset = config.weight_offset                  # v8.0.21: 非负CS段权重偏移量
        self.min_quota = config.min_quota_per_industry


    def calculate_quotas(self, pairs_manager) -> Dict[str, Dict]:
        """
        计算每个行业的配对配额 (v8.0.14: 集中度检测前置优化)

        Args:
            pairs_manager: PairsManager实例

        Returns:
            {industry_code: {
                'quota': int,           # 分配的配额数量
                'weight': int,          # 计算的权重值
                'composite_score': float  # 综合得分
            }}

            - 预热期: 返回空字典 {}
            - 正常期: 返回55个行业的配额字典
        """

        # 步骤1: 检查预热期
        if self._is_in_warmup_period():
            days_running = (self.algorithm.Time - self.algorithm.StartDate).days
            warmup_days = self.algorithm.config.industry_quota.warmup_days
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({days_running}/{warmup_days}天), "
                f"暂不分配配额"
            )
            return {}

        # 步骤2: 获取集中度过高的行业 (v8.0.14: 移到权重计算之前)
        # 原因: 超标行业不应占用配额池份额，weight需在计算前置0
        over_concentrated = pairs_manager.check_industry_concentration()

        # 步骤3: 遍历55个行业,计算权重
        industry_weights = {}
        total_weight = 0

        industry_codes = self._get_all_industry_codes()

        for industry_code in industry_codes:
            # v8.0.14: 集中度超标的行业，权重置0，不参与配额分配
            if industry_code in over_concentrated:
                cs = 0.0
                weight = 0
            else:
                # 从PairsManager获取composite_score
                cs = pairs_manager.get_industry_composite_score(industry_code)
                # 无历史数据时cs=0, weight自动=1
                weight = self._calculate_weight(cs)

            industry_weights[industry_code] = {
                'composite_score': cs,
                'weight': weight
            }
            total_weight += weight

        # 步骤4: 按权重比例分配配额 (向下取整)
        industry_quotas = {}

        for industry_code, data in industry_weights.items():
            # v8.0.14: weight=0的行业(含集中度超标)，配额自动为0
            if data['weight'] == 0:
                quota = 0
            elif total_weight > 0:
                quota = int(np.floor(self.total_quota * data['weight'] / total_weight))
                # 保底机制
                if quota < self.min_quota:
                    quota = self.min_quota
            else:
                # 极端情况: 所有行业权重=0 (不应该发生)
                quota = self.min_quota

            industry_quotas[industry_code] = {
                'quota': quota,
                'weight': data['weight'],
                'composite_score': data['composite_score']
            }

        # 步骤5: 详细日志
        self._log_quota_allocation(industry_quotas)

        return industry_quotas


    def apply_quotas(self, coint_result: Dict) -> List:
        """
        应用行业配额到协整结果 (v7.82.0: 随机抽取,避免pvalue过度加权)

        Args:
            coint_result: CointegrationAnalyzer.cointegration_procedure()返回值
                {'pairs': [...], 'statistics': {...}}

        Returns:
            List[Dict]: 应用配额后的配对列表
                每个Dict: {'symbol1', 'symbol2', 'pvalue', 'industry_code'}

        设计理念 (v7.82.0):
        - CointegrationAnalyzer已用pvalue<0.01严格筛选
        - 进入本方法的配对pvalue都在0.0000x~0.0099 (极窄区间)
        - 继续用pvalue排序 = 过度放大微小差异,导致"赢家通吃"
        - 随机抽取 = 给所有通过协整的配对公平机会,增加多样性

        实现:
        1. 获取行业配额
        2. 按行业分组
        3. 确定性随机抽取 (种子=hash(日期+行业), 保证可复现)
        4. 应用max_repeats约束 (单股最多参与N对)
        5. 返回选定配对合并列表
        """
        # 步骤1: 获取行业配额
        industry_quotas = self.calculate_quotas(self.algorithm.pairs_manager)

        # 步骤2: 按行业分组pairs
        raw_pairs = coint_result['pairs']
        industry_groups = defaultdict(list)

        for pair in raw_pairs:
            industry_code = str(pair['industry_code'])
            industry_groups[industry_code].append(pair)

        # 步骤3-4: 对每个行业应用配额和单股限制
        selected_pairs = []

        for industry_code, pairs in industry_groups.items():
            # 获取配额(优先使用动态配额,否则使用最低保底配额)
            quota_info = industry_quotas.get(industry_code)
            quota = quota_info['quota'] if quota_info else self.min_quota

            # 设置确定性随机种子 (保证每月每行业结果一致)
            seed = hash(f"{self.algorithm.Time.date()}_{industry_code}") % (2**32)
            random.seed(seed)

            # 随机打乱配对顺序
            shuffled_pairs = pairs.copy()
            random.shuffle(shuffled_pairs)

            # 顺序遍历(等价于随机抽取) + max_repeats约束
            symbol_counts = defaultdict(int)
            industry_selected = []
            max_symbol_repeats = self.algorithm.config.cointegration_analyzer.max_symbol_repeats

            for pair in shuffled_pairs:
                if len(industry_selected) >= quota:
                    break

                s1, s2 = pair['symbol1'], pair['symbol2']
                if (symbol_counts[s1] < max_symbol_repeats and
                    symbol_counts[s2] < max_symbol_repeats):
                    industry_selected.append(pair)
                    symbol_counts[s1] += 1
                    symbol_counts[s2] += 1

            selected_pairs.extend(industry_selected)

        # 步骤6: 返回所有选定配对
        return selected_pairs


    def _calculate_weight(self, composite_score: float) -> int:
        """
        计算行业权重 (v8.0.21: 分段指数函数)

        公式:
            f(x) = ceil(e^x)       当 x < 0   (亏损行业 → 权重1)
            f(x) = ceil(e^(kx)+c)  当 x >= 0  (盈利行业 → 权重3~9)

        参数:
            k = exp_scale_factor (默认6.0)
            c = weight_offset (默认2)

        Args:
            composite_score: 行业综合得分 (ROI × WIN_RATE)

        Returns:
            权重值 (整数, 最小为1)

        示例:
            cs=-0.10 → weight=1  (亏损行业最低配额)
            cs=0.00  → weight=3  (盈亏平衡起点)
            cs=0.10  → weight=4
            cs=0.20  → weight=6
            cs=0.30  → weight=9  (优秀行业峰值)

        设计理由:
            - 亏损行业保留最低配额1，避免完全冻结（死局）
            - 盈利行业起点为3，与亏损行业拉开差距
            - 峰值约9，相对起点3有3倍差距，奖励优秀行业
        """
        import numpy as np

        if composite_score < 0:
            # 亏损行业: ceil(e^x) → 实际都是1 (因为e^(-0.1)≈0.9, ceil=1)
            raw_weight = int(np.ceil(np.exp(composite_score)))
        else:
            # 盈利行业: ceil(e^(kx) + c), 起点3, 峰值约9
            raw_weight = int(np.ceil(np.exp(self.exp_scale_factor * composite_score) + self.weight_offset))

        return max(1, raw_weight)  # 保底1


    def _get_all_industry_codes(self) -> List[str]:
        """
        获取所有55个行业代码 (v7.71.0: 从config.constants读取)

        Returns:
            MorningstarIndustryGroupCode列表 (字符串格式)
        """
        industry_names = self.algorithm.config.constants['industry_names']
        return [str(code) for code in industry_names.keys()]


    def _is_in_warmup_period(self) -> bool:
        """
        检查是否在预热期 (v8.0.26: 配置路径更新)

        Returns:
            True: 在预热期 (days_running < warmup_days)
            False: 已过预热期
        """
        days_running = (self.algorithm.Time - self.algorithm.StartDate).days
        warmup_days = self.algorithm.config.industry_quota.warmup_days
        return days_running < warmup_days


    def _log_quota_allocation(self, industry_quotas: Dict):
        """
        输出详细配额分配日志 (v7.71.0)

        Args:
            industry_quotas: 行业配额字典
        """
        industry_names = self.algorithm.config.constants['industry_names']

        # 统计信息
        total_allocated = sum(q['quota'] for q in industry_quotas.values())
        positive_cs_count = sum(1 for q in industry_quotas.values() if q['composite_score'] > 0)

        self.algorithm.Debug(
            f"[行业配额] 全局配额={self.total_quota}, "
            f"实际分配={total_allocated}, "
            f"覆盖行业={len(industry_quotas)}, "
            f"正收益行业={positive_cs_count}"
        )

        # 按配额降序排序,只显示TOP10
        sorted_quotas = sorted(
            industry_quotas.items(),
            key=lambda x: x[1]['quota'],
            reverse=True
        )

        for industry_code, data in sorted_quotas[:10]:
            industry_name = industry_names.get(int(industry_code), f'未知({industry_code})')
            self.algorithm.Debug(
                f"[行业配额] {industry_name}: "
                f"CS={data['composite_score']*100:+.2f}% → "
                f"权重={data['weight']} → "
                f"配额={data['quota']}",
                level=1
            )
