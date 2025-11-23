# region imports
from AlgorithmImports import *
from typing import Dict
from collections import defaultdict
# endregion


class IndustryQuotaManager:
    """
    行业配额管理器 - 动态调整每个行业的配对数量上限 (v7.71.0: 指数权重系统)

    核心逻辑:
    - 预热期(前90天): 返回空字典,不分配配额
    - 正常期: 根据composite_score使用指数权重分配全局配额15

    权重函数:
        f(x) = ceil(e^x)      当 x ≤ 0  (负CS/零CS,权重=1)
        f(x) = ceil(e^(8x))   当 x > 0  (正CS,指数增长)

        其中 x = composite_score = industry_roi × win_rate

    配额分配:
        - 全局约束: 总配额=15
        - 权重比例: quota = floor(15 × weight / total_weight)
        - 保底机制: 每个行业最少1个配额

    设计特点:
    - 数据驱动: 从PairsManager读取composite_score
    - 无状态: 每次calculate_quotas()即时计算
    - 行业全覆盖: 遍历55个MorningstarIndustryGroupCode
    """

    def __init__(self, algorithm, config: 'IndustryQuotaManagerConfig'):
        """
        初始化行业配额管理器 (v7.71.0: 简化配置)

        Args:
            algorithm: QCAlgorithm实例
            config: IndustryQuotaManagerConfig实例
        """
        self.algorithm = algorithm
        self.total_quota = config.total_quota
        self.warmup_days = config.warmup_days
        self.exp_scale_factor = config.exp_scale_factor
        self.min_quota = config.min_quota_per_industry


    def _is_in_warmup_period(self) -> bool:
        """
        检查是否在预热期

        Returns:
            True: 在预热期 (days_running < warmup_days)
            False: 已过预热期
        """
        days_running = (self.algorithm.Time - self.algorithm.StartDate).days
        return days_running < self.warmup_days


    def _calculate_weight(self, composite_score: float) -> int:
        """
        计算行业权重 (v7.71.0: 指数分段函数, c=8)

        公式:
            f(x) = ceil(e^x)      当 x ≤ 0
            f(x) = ceil(e^(8x))   当 x > 0

        Args:
            composite_score: 行业综合得分 (ROI × WIN_RATE)
                - 无历史数据时: cs=0 → weight=ceil(e^0)=1
                - 负收益: cs<0 → weight=1
                - 正收益: cs>0 → 指数增长

        Returns:
            权重值 (整数)

        示例:
            cs=-0.10 → weight=1
            cs=0.00  → weight=1
            cs=0.05  → weight=2
            cs=0.10  → weight=3
            cs=0.20  → weight=5
            cs=0.30  → weight=12
        """
        import numpy as np

        if composite_score <= 0:
            # 负CS/零CS: e^x ≈ 1 (x≤0)
            return int(np.ceil(np.exp(composite_score)))
        else:
            # 正CS: 指数增长 (c=8)
            return int(np.ceil(np.exp(self.exp_scale_factor * composite_score)))


    def _get_all_industry_codes(self) -> List[str]:
        """
        获取所有55个行业代码 (v7.71.0: 从config.constants读取)

        Returns:
            MorningstarIndustryGroupCode列表 (字符串格式)
        """
        industry_names = self.algorithm.config.constants['industry_names']
        return [str(code) for code in industry_names.keys()]


    def calculate_quotas(self, pairs_manager) -> Dict[str, Dict]:
        """
        计算每个行业的配对配额 (v7.71.0: 指数权重分配)

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
        import numpy as np

        # 步骤1: 检查预热期
        if self._is_in_warmup_period():
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({(self.algorithm.Time - self.algorithm.StartDate).days}/{self.warmup_days}天), "
                f"暂不分配配额"
            )
            return {}

        # 步骤2: 遍历55个行业,计算权重
        industry_weights = {}
        total_weight = 0

        industry_codes = self._get_all_industry_codes()

        for industry_code in industry_codes:
            # 从PairsManager获取composite_score
            cs = pairs_manager._calculate_composite_score(industry_code)
            # 无历史数据时cs=0, weight自动=1
            weight = self._calculate_weight(cs)

            industry_weights[industry_code] = {
                'composite_score': cs,
                'weight': weight
            }
            total_weight += weight

        # 步骤3: 按权重比例分配配额 (向下取整)
        industry_quotas = {}

        for industry_code, data in industry_weights.items():
            if total_weight > 0:
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

        # 步骤4: 详细日志
        self._log_quota_allocation(industry_quotas)

        return industry_quotas


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


    def apply_quotas(self, coint_result: Dict) -> List:
        """
        应用行业配额到协整结果

        Args:
            coint_result: CointegrationAnalyzer.cointegration_procedure()返回值
                {'raw_pairs': [...], 'statistics': {...}}

        Returns:
            List[Dict]: 应用配额后的配对列表
                每个Dict: {'symbol1', 'symbol2', 'pvalue', 'industry_code'}

        实现:
        1. 获取行业配额
        2. 按行业分组并按pvalue排序
        3. 贪心算法应用配额和单股重复限制
        4. 返回选定配对合并列表
        """
        # 步骤1: 获取行业配额
        industry_quotas = self.calculate_quotas(self.algorithm.pairs_manager)

        # 步骤2: 按行业分组raw_pairs
        raw_pairs = coint_result['raw_pairs']
        industry_groups = defaultdict(list)

        for pair in raw_pairs:
            industry_code = str(pair['industry_code'])
            industry_groups[industry_code].append(pair)

        # 步骤3-4: 对每个行业应用配额和单股限制
        selected_pairs = []
        industry_names = self.algorithm.config.constants['industry_names']

        for industry_code, pairs in industry_groups.items():
            # 按pvalue排序(从小到大)
            sorted_pairs = sorted(pairs, key=lambda x: x['pvalue'])

            # 获取配额(优先使用动态配额,否则使用最低保底配额)
            quota_info = industry_quotas.get(industry_code)
            quota = quota_info['quota'] if quota_info else self.min_quota

            # 贪心选择(同时检查配额和单股重复限制)
            symbol_counts = defaultdict(int)
            industry_selected = []
            max_symbol_repeats = self.algorithm.config.cointegration_analyzer.max_symbol_repeats

            for pair in sorted_pairs:
                if len(industry_selected) >= quota:
                    break

                s1, s2 = pair['symbol1'], pair['symbol2']
                if (symbol_counts[s1] < max_symbol_repeats and
                    symbol_counts[s2] < max_symbol_repeats):
                    industry_selected.append(pair)
                    symbol_counts[s1] += 1
                    symbol_counts[s2] += 1

            # 步骤5: 输出配额应用日志(只记录有配对的行业)
            if len(industry_selected) > 0:
                industry_name = industry_names.get(int(industry_code), f'未知({industry_code})')
                self.algorithm.Debug(
                    f"[配额筛选] {industry_name}: "
                    f"协整通过{len(sorted_pairs)}对 → 配额{quota} → 最终选取{len(industry_selected)}对",
                    level=1
                )

            selected_pairs.extend(industry_selected)

        # 步骤6: 返回所有选定配对
        return selected_pairs
