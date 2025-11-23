# region imports
from AlgorithmImports import *
from typing import Dict
from collections import defaultdict
# endregion


class IndustryQuotaManager:
    """
    行业配额管理器 - 动态调整每个行业的配对数量上限

    核心逻辑:
    - 预热期(前90天): 所有行业默认配额 = 1
    - 正常期: 每月根据industry_return动态调整配额

    配额分层:
        <0%       → 1个配对 (tier0)
        [0%,5%)   → 2个配对 (tier1)
        [5%,10%)  → 3个配对 (tier2)
        [10%,20%) → 5个配对 (tier3)
        [20%,30%) → 8个配对 (tier4)
        ≥30%      → 10个配对 (tier5)

    设计特点:
    - 数据驱动: 从Pairs对象读取交易统计
    - 无状态: 每次调用calculate_quotas()即时计算
    - 行业聚合: 按MorningstarIndustryGroupCode分组
    """

    def __init__(self, algorithm, config: 'IndustryQuotaManagerConfig'):
        """
        初始化行业配额管理器

        Args:
            algorithm: QCAlgorithm实例
            config: IndustryQuotaManagerConfig实例
        """
        self.algorithm = algorithm
        self.warmup_days = config.warmup_days
        self.tier_thresholds = config.tier_thresholds
        self.tier_quotas = config.tier_quotas
        # 默认配额使用tier0配额 (预热期和回退场景)
        self.default_quota = config.tier_quotas['tier0']


    def _is_in_warmup_period(self) -> bool:
        """
        检查是否在预热期

        Returns:
            True: 在预热期 (days_running < warmup_days)
            False: 已过预热期
        """
        days_running = (self.algorithm.Time - self.algorithm.StartDate).days
        return days_running < self.warmup_days


    def calculate_quotas(self, pairs_manager) -> Dict[str, Dict]:
        """
        计算每个行业的配对配额

        Args:
            pairs_manager: PairsManager实例

        Returns:
            {industry_code: {'quota': int, 'tier': str, 'industry_return': float}}
            - 预热期: 返回空字典 (使用tier_quotas['tier0'])
            - 正常期: 根据industry_return返回分层配额
            - 无历史数据: 返回tier_quotas['tier0']
        """
        # 步骤1: 检查预热期 (v7.66.0: 提取为私有方法)
        if self._is_in_warmup_period():
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({(self.algorithm.Time - self.algorithm.StartDate).days}/{self.warmup_days}天), "
                f"所有行业使用默认配额: {self.default_quota}"
            )
            return {}

        # 步骤2: 从PairsManager获取聚合数据 (v7.66.0: 委托调用,消除重复遍历)
        industry_data_dict = pairs_manager._aggregate_all_industry_data()

        # 步骤3: 计算每个行业的配额和tier
        industry_quotas = {}
        industry_names = self.algorithm.config.constants['industry_names']

        for industry_code, data in industry_data_dict.items():
            # 跳过无历史交易的行业
            if data.past_invested_capital <= 0:
                continue

            # 计算行业收益率 (v7.66.0: 术语统一)
            industry_return = data.realized_pnl / data.past_invested_capital
            quota = self._get_quota_by_return(industry_return)
            tier = self._get_tier_by_return(industry_return)

            # 返回包含tier信息的字典
            industry_quotas[industry_code] = {
                'quota': quota,
                'tier': tier,
                'industry_return': industry_return  # v7.66.0: 术语统一
            }

            # 详细日志 - 显示非默认配额的计算依据
            if quota != self.default_quota:
                industry_name = industry_names.get(int(industry_code), f'未知({industry_code})')
                self.algorithm.Debug(
                    f"[行业配额] {industry_name}: "
                    f"累计收益{industry_return*100:+.2f}% "
                    f"(PnL=${data.realized_pnl:,.0f}, Cost=${data.past_invested_capital:,.0f}, 交易{data.trade_count}对) "
                    f"→ 配额: {self.default_quota} → {quota} (tier={tier})",
                    level=1
                )

        # 汇总日志
        non_default = {k: v for k, v in industry_quotas.items() if v['quota'] != self.default_quota}
        if not non_default:
            self.algorithm.Debug(
                f"[行业配额] 本月所有行业使用默认配额: {self.default_quota}"
            )

        return industry_quotas


    def _get_quota_by_return(self, industry_return: float) -> int:
        """
        根据行业收益率计算配额

        Args:
            industry_return: 行业收益率 (小数,如0.05表示5%)

        Returns:
            配额数量 (1/2/3/5/8/10)
        """
        if industry_return < self.tier_thresholds['tier0']:
            return self.tier_quotas['tier0']
        elif industry_return < self.tier_thresholds['tier1']:
            return self.tier_quotas['tier1']
        elif industry_return < self.tier_thresholds['tier2']:
            return self.tier_quotas['tier2']
        elif industry_return < self.tier_thresholds['tier3']:
            return self.tier_quotas['tier3']
        elif industry_return < self.tier_thresholds['tier4']:  # <30%
            return self.tier_quotas['tier4']
        else:  # ≥30%
            return self.tier_quotas['tier5']  # 超高收益行业


    def _get_tier_by_return(self, industry_return: float) -> str:
        """
        根据行业收益率计算tier

        Args:
            industry_return: 行业收益率 (小数)

        Returns:
            tier名称 ('tier0'/'tier1'/'tier2'/'tier3'/'tier4'/'tier5')
        """
        if industry_return < self.tier_thresholds['tier0']:
            return 'tier0'
        elif industry_return < self.tier_thresholds['tier1']:
            return 'tier1'
        elif industry_return < self.tier_thresholds['tier2']:
            return 'tier2'
        elif industry_return < self.tier_thresholds['tier3']:
            return 'tier3'
        elif industry_return < self.tier_thresholds['tier4']:  # <30%
            return 'tier4'
        else:  # ≥30%
            return 'tier5'  # 超高收益行业


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

            # 获取配额(优先使用动态配额,否则使用默认配额)
            quota_info = industry_quotas.get(industry_code)
            quota = quota_info['quota'] if quota_info else self.default_quota

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
