# region imports
from AlgorithmImports import *
from typing import Dict, Tuple
from collections import defaultdict
import random
import numpy as np
# endregion


class IndustryQuotaManager:
    """
    行业配额管理器 - 控制每个行业的配对数量上限 (v8.2.3: 预热期正常交易)

    核心流程:
        1. calculate_quotas(): 根据行业历史表现计算每个行业的配额权重
           - 预热期(前90天): CS=0 → weight=1 → 均等分配 (v8.2.3)
           - 正常期: 基于composite_score的指数权重 ceil(e^(6×cs))

        2. apply_quotas(): 两轮配额分配
           - 第一轮: 按权重分配配额，各行业按实际需求领取，退回盈余
           - 第二轮: 盈余再分配给需求充足但配额不足的行业
           - 活水效应: 高CS行业配对不足时，剩余配额自动补给低CS但配对充足的行业
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
        # v8.2.3: 删除weight_offset (比例分配时offset无意义)
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

            v8.2.3: 预热期也返回配额字典 (所有行业CS=0 → weight=1 → 均等分配)
        """

        # 步骤1: 检查预热期 (v8.2.3: 不再return空字典)
        if self._is_in_warmup_period():
            days_running = (self.algorithm.Time - self.algorithm.StartDate).days
            warmup_days = self.algorithm.config.industry_quota.warmup_days
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({days_running}/{warmup_days}天), "
                f"使用均等配额 (CS=0 → 权重=1)"
            )
            # v8.2.3: 继续走正常流程，不return空字典
            # 预热期所有行业CS=0 → weight=1 → 均等分配配额

        # 步骤2: 获取集中度过高的行业 (v8.1.7: 仅用于冻结demand,不影响weight)
        over_concentrated = pairs_manager.check_industry_concentration()
        # 缓存供 apply_quotas() 使用
        self._over_concentrated = over_concentrated

        # 步骤3: 遍历55个行业,计算权重 (v8.1.7: 权重不受集中度影响)
        industry_weights = {}
        total_weight = 0

        industry_codes = self._get_all_industry_codes()

        for industry_code in industry_codes:
            # v8.1.7: 权重基于历史表现计算，不受集中度影响
            # 原因: weight是"历史身份证"，集中度超标应冻结demand而非篡改weight
            cs = pairs_manager.get_industry_composite_score(industry_code)
            weight = self._calculate_weight(cs)

            industry_weights[industry_code] = {
                'composite_score': cs,
                'weight': weight
            }
            total_weight += weight

        # 步骤4: 按权重比例分配配额 (v8.1.7: 移除weight=0特殊处理)
        industry_quotas = {}

        for industry_code, data in industry_weights.items():
            # v8.1.7: 统一按权重比例计算配额 (不再有weight=0的情况)
            if total_weight > 0:
                quota = int(np.floor(self.total_quota * data['weight'] / total_weight))
                # 保底机制
                if quota < self.min_quota:
                    quota = self.min_quota
            else:
                # 极端情况: 所有行业权重=0 (不应该发生,因为最低权重=1)
                quota = self.min_quota

            industry_quotas[industry_code] = {
                'quota': quota,
                'weight': data['weight'],
                'composite_score': data['composite_score']
            }

        # 步骤5: 行业概览日志 (v8.1.2: 精简输出)
        self._log_industry_overview(industry_quotas)

        return industry_quotas


    def apply_quotas(self, coint_result: Dict) -> List:
        """
        两轮配额分配 (v8.0.18)

        第一轮: 按CS权重分配配额，各行业按实际需求领取，退回盈余
        第二轮: 盈余再分配给需求充足但配额不足的行业

        Args:
            coint_result: CointegrationAnalyzer.cointegration_procedure()返回值
                {'pairs': [...], 'statistics': {...}}

        Returns:
            List[Dict]: 应用配额后的配对列表

        设计理念 (v8.0.18 Two-Pass Allocation):
        - 旧机制: min_quota=1保底 → 55行业各得1 → 超出total_quota=25
        - 新机制: 按需领取 → 配额盈余自动流向需求充足的行业
        - 活水效应: 高CS行业配对不足时，剩余配额自动补给低CS但配对充足的行业
        """
        raw_pairs = coint_result['pairs']

        # 步骤0: 预处理 - 计算每个行业的需求(协整配对数)
        industry_demands = self._count_pairs_by_industry(raw_pairs)

        # 步骤1: 计算权重 (复用现有逻辑)
        industry_quotas = self.calculate_quotas(self.algorithm.pairs_manager)
        industry_weights = {
            code: data['weight']
            for code, data in industry_quotas.items()
        }

        # 步骤1.5: 冻结集中度超标行业的需求 (v8.1.7)
        # 原因: 集中度超标应冻结demand(本轮禁止开仓),而非篡改weight(历史身份证)
        over_concentrated = getattr(self, '_over_concentrated', set())
        frozen_count = 0
        for code in over_concentrated:
            if code in industry_demands:
                industry_demands[code] = 0
                frozen_count += 1

        # v8.1.7: 输出冻结日志
        if over_concentrated:
            industry_names = self.algorithm.config.constants['industry_names']
            frozen_names = [
                industry_names.get(int(code), code)[:4]
                for code in over_concentrated
            ]
            self.algorithm.Debug(
                f"[配额冻结] {len(over_concentrated)}个行业集中度超标 → 需求置0: {', '.join(frozen_names[:5])}",
                level=1
            )

        # 步骤2: 第一轮分配
        first_pass, quota_pool, hungry_industries = self._first_pass_allocation(
            industry_weights, industry_demands
        )

        # 步骤3: 第二轮再分配
        final_quotas = self._second_pass_redistribution(
            first_pass, quota_pool, hungry_industries, industry_weights
        )

        # 步骤4: 日志输出 (v8.1.2: 只输出最终结果)
        self._log_final_allocation(final_quotas)

        # 步骤5: 应用配额到配对 (复用随机抽取逻辑)
        return self._apply_final_quotas(raw_pairs, final_quotas)


    def _calculate_weight(self, composite_score: float) -> int:
        """
        计算行业权重 (v8.2.3: 统一公式)

        公式: ceil(e^(6×cs))

        Args:
            composite_score: 行业综合得分 (ROI × WIN_RATE), 取值范围 [0, +∞)
                           (v8.2.3: CS不再为负，ROI<0时返回0)

        Returns:
            权重值 (整数, 最小为1)

        示例:
            cs=0.00 → weight=1  (预热期/无数据行业)
            cs=0.05 → weight=2
            cs=0.10 → weight=2
            cs=0.15 → weight=3
            cs=0.20 → weight=4
            cs=0.25 → weight=5
            cs=0.30 → weight=7  (优秀行业)
        """
        import numpy as np

        # v8.2.3: 统一公式，无分段
        raw_weight = int(np.ceil(np.exp(self.exp_scale_factor * composite_score)))

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


    def _count_pairs_by_industry(self, pairs: List) -> Dict[str, int]:
        """
        统计每个行业的协整配对数量 (demand)

        Args:
            pairs: 协整配对列表

        Returns:
            {industry_code: 配对数量}
        """
        demands = defaultdict(int)
        for pair in pairs:
            industry_code = str(pair['industry_code'])
            demands[industry_code] += 1
        return dict(demands)


    def _first_pass_allocation(
        self,
        weights: Dict[str, int],
        demands: Dict[str, int]
    ) -> Tuple[Dict[str, int], int, Dict[str, int]]:
        """
        第一轮配额分配 (v8.1.1: 需求驱动配额分配)

        按权重分配配额，各行业按实际需求领取，退回盈余

        Args:
            weights: {industry_code: weight} 行业权重
            demands: {industry_code: demand} 行业需求(协整配对数)

        Returns:
            first_pass_selected: {industry_code: selected_count} 第一轮选中数
            quota_pool: 退回的配额总数
            hungry_industries: {industry_code: unmet_demand} 未满足需求的行业

        v8.1.1 修复:
            - 分母改为只计算有协整配对行业的权重总和 (10-20个,而非全部55个)
            - floor改round,提高配额填充率 (轻微超发可接受)
            - 原问题: floor(25×1/56)=0 → 所有行业配额为0
            - 修复后: round(25×1/10)=3 → 每个有配对行业分配2-3个配额
        """
        # v8.1.1: 只计算有协整配对行业的权重总和 (需求驱动)
        total_weight = sum(
            weight for code, weight in weights.items()
            if demands.get(code, 0) > 0
        )

        first_pass_selected = {}
        quota_pool = 0
        hungry_industries = {}

        for industry_code, weight in weights.items():
            # 获取需求 (没有协整配对则为0)
            demand = demands.get(industry_code, 0)

            # v8.1.1: 无需求行业直接跳过,配额=0
            if demand == 0:
                first_pass_selected[industry_code] = 0
                continue

            # v8.1.1: 计算配额 (round四舍五入,提高填充率)
            if total_weight > 0:
                quota = round(self.total_quota * weight / total_weight)
            else:
                quota = 0

            # 选中数 = min(配额, 需求)
            selected = min(quota, demand)
            first_pass_selected[industry_code] = selected

            # 计算剩余
            if quota > demand:
                quota_pool += (quota - demand)  # 配额没用完 → 退回池
            elif demand > quota:
                hungry_industries[industry_code] = demand - quota  # 需求没满足 → 等待bonus

        return first_pass_selected, quota_pool, hungry_industries


    def _second_pass_redistribution(
        self,
        first_pass: Dict[str, int],
        quota_pool: int,
        hungry: Dict[str, int],
        weights: Dict[str, int]
    ) -> Dict[str, int]:
        """
        第二轮配额再分配 (v8.0.18)

        将退回的配额按权重比例分配给饥渴行业

        Args:
            first_pass: {industry_code: selected} 第一轮选中数
            quota_pool: 退回的配额总数
            hungry: {industry_code: unmet_demand} 饥渴行业
            weights: {industry_code: weight} 行业权重

        Returns:
            final_selected: {industry_code: final_count} 最终选中数
        """
        if quota_pool == 0 or not hungry:
            return first_pass

        final_selected = first_pass.copy()

        # 计算饥渴行业的权重总和
        hungry_weight_sum = sum(weights.get(ind, 1) for ind in hungry.keys())

        # v8.1.7: 防御性除零保护 (理论上不会触发,因为最低权重=1)
        # 保留原因: 防止未来代码变更引入边界情况
        if hungry_weight_sum <= 0:
            return first_pass

        remaining_pool = quota_pool

        for industry_code, unmet_demand in hungry.items():
            if remaining_pool <= 0:
                break

            # 按权重比例分配 (v8.1.7: 移除循环内的冗余除零检查)
            weight = weights.get(industry_code, 1)
            bonus = int(np.floor(quota_pool * weight / hungry_weight_sum))

            # 受限于未满足需求和剩余配额池
            actual_bonus = min(bonus, unmet_demand, remaining_pool)

            final_selected[industry_code] = first_pass.get(industry_code, 0) + actual_bonus
            remaining_pool -= actual_bonus

        return final_selected


    def _apply_final_quotas(
        self,
        raw_pairs: List,
        final_quotas: Dict[str, int]
    ) -> List:
        """
        应用最终配额到配对列表 (v8.0.18)

        对每个行业进行确定性随机抽取，并应用单股重复限制

        Args:
            raw_pairs: 原始协整配对列表
            final_quotas: {industry_code: quota} 最终配额

        Returns:
            选中的配对列表
        """
        # 按行业分组
        industry_groups = defaultdict(list)
        for pair in raw_pairs:
            industry_code = str(pair['industry_code'])
            industry_groups[industry_code].append(pair)

        selected_pairs = []
        max_symbol_repeats = self.algorithm.config.cointegration_analyzer.max_symbol_repeats

        for industry_code, pairs in industry_groups.items():
            quota = final_quotas.get(industry_code, 0)
            if quota == 0:
                continue

            # 设置确定性随机种子 (保证每月每行业结果一致)
            seed = hash(f"{self.algorithm.Time.date()}_{industry_code}") % (2**32)
            random.seed(seed)

            # 随机打乱配对顺序
            shuffled_pairs = pairs.copy()
            random.shuffle(shuffled_pairs)

            # 顺序遍历(等价于随机抽取) + max_repeats约束
            symbol_counts = defaultdict(int)
            industry_selected = []

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

        return selected_pairs


    def _log_final_allocation(
        self,
        final_quotas: Dict[str, int]
    ):
        """
        输出最终配额分配结果 (v8.1.2: 精简日志)

        格式: [配额分配] 服装制造:5, 房建:2, ... 共{total}对

        Args:
            final_quotas: 最终配额 {industry_code: quota}
        """
        industry_names = self.algorithm.config.constants['industry_names']

        # 只保留有配额的行业
        allocated = {code: quota for code, quota in final_quotas.items() if quota > 0}
        if not allocated:
            self.algorithm.Debug("[配额分配] 无有效配额")
            return

        # 按配额降序排序
        sorted_alloc = sorted(allocated.items(), key=lambda x: -x[1])

        # 格式化: 行业名:配额数
        parts = []
        for code, quota in sorted_alloc:
            name = industry_names.get(int(code), f'未知({code})')
            # 截取行业名前4字符以保持简洁
            short_name = name[:4] if len(name) > 4 else name
            parts.append(f"{short_name}:{quota}")

        total = sum(allocated.values())
        self.algorithm.Debug(f"[配额分配] {', '.join(parts)} 共{total}对")


    def _log_industry_overview(self, industry_quotas: Dict):
        """
        输出行业ROI概览 (v8.1.5: 改用ROI分类)

        格式: [行业概览] 正收益=X个 → TOP3 | 负收益=Y个 → TOP3

        Args:
            industry_quotas: 行业配额字典 {code: {composite_score, weight, quota}}
        """
        industry_names = self.algorithm.config.constants['industry_names']

        # v8.1.5: 改用 ROI 分类，而非 CS
        positive = []
        negative = []
        for code, data in industry_quotas.items():
            roi = self.algorithm.pairs_manager.get_industry_roi(code)
            if roi > 0:
                positive.append((code, roi))
            elif roi < 0:
                negative.append((code, roi))

        # 排序取TOP3
        positive.sort(key=lambda x: -x[1])
        negative.sort(key=lambda x: x[1])

        def format_top3(items):
            if not items:
                return "无"
            parts = []
            for code, score in items[:3]:
                name = industry_names.get(int(code), f'未知({code})')
                short_name = name[:4] if len(name) > 4 else name
                parts.append(f"{short_name}({score*100:+.1f}%)")
            return ', '.join(parts)

        pos_str = format_top3(positive)
        neg_str = format_top3(negative)

        self.algorithm.Debug(
            f"[行业概览] 正收益={len(positive)}个 → {pos_str} | "
            f"负收益={len(negative)}个 → {neg_str}"
        )
