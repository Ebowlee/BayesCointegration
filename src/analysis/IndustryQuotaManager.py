# region imports
from AlgorithmImports import *
from typing import Dict
from collections import defaultdict
# endregion


class IndustryQuotaManager:
    """
    行业配额管理器 - 动态调整每个行业的配对数量上限 (v7.12.0)

    核心逻辑:
    - 前180天: 所有行业默认配额 = 1 (预热期, 收集历史数据)
    - 180天后: 每月根据加权收益率动态调整配额 (4档配额: 1/3/6/9)

    加权收益率计算:
        weighted_return = sum(total_pnl_dollars) / sum(total_pair_cost)

    配额分层:
        ≤0.05 (5%)   → 1个配对
        ≤0.10 (10%)  → 3个配对
        ≤0.20 (20%)  → 6个配对
        >0.20 (20%+) → 9个配对

    设计特点:
    - 数据驱动: 从Pairs对象读取交易历史 (trade_count, total_pnl_dollars, total_pair_cost)
    - 无状态: 每次调用calculate_quotas()即时计算,不存储历史数据
    - 行业聚合: 按MorningstarIndustryGroupCode分组聚合收益
    - 动态调整: 每月更新一次配额 (在协整检测前调用)

    使用示例:
    ```python
    # main.py初始化:
    self.industry_quota_manager = IndustryQuotaManager(self, config.industry_quota)

    # 月度协整检测前:
    industry_quotas = self.industry_quota_manager.calculate_quotas(
        pairs_manager=self.pairs_manager
    )
    # Returns: {'100': 3, '101': 6, '102': 1, ...}

    # CointegrationAnalyzer应用配额:
    analyzer = CointegrationAnalyzer(algorithm, config, industry_quotas)
    ```

    配置示例:
    {
        'warmup_days': 180,
        'default_quota': 1,
        'tier_thresholds': {'tier1': 0.05, 'tier2': 0.10, 'tier3': 0.20},
        'tier_quotas': {'tier1': 1, 'tier2': 3, 'tier3': 6, 'tier4': 9}
    }
    """

    def __init__(self, algorithm, config):
        """
        初始化行业配额管理器

        Args:
            algorithm: QCAlgorithm实例
            config: IndustryQuotaConfig dataclass实例
        """
        self.algorithm = algorithm
        self.warmup_days = config.warmup_days
        self.default_quota = config.default_quota
        self.tier_thresholds = config.tier_thresholds
        self.tier_quotas = config.tier_quotas


    def calculate_quotas(self, pairs_manager) -> Dict[str, Dict]:
        """
        计算每个行业的配对配额 (v7.32.0: 返回包含tier信息的字典)

        Args:
            pairs_manager: PairsManager实例,用于访问所有Pairs对象

        Returns:
            {industry_code: {'quota': int, 'tier': str, 'weighted_return': float}} 字典
            - 预热期: 返回空字典 (CointegrationAnalyzer使用default_quota, PairSelector使用默认tier0)
            - 正常期: 根据加权收益率返回分层配额和tier信息
            - 无历史数据的行业: 返回default_quota和tier0

        实现逻辑:
        1. 检查是否过了预热期 (algorithm.Time - algorithm.StartDate > warmup_days)
        2. 如果预热期: 返回空字典 (CointegrationAnalyzer使用default_quota)
        3. 如果正常期:
            a. 遍历所有Pairs对象,聚合每个行业的total_pnl_dollars和total_pair_cost
            b. 计算每个行业的加权收益率 = sum(pnl) / sum(cost)
            c. 根据收益率分层,返回对应配额和tier信息
        """
        # 步骤1: 检查预热期
        days_running = (self.algorithm.Time - self.algorithm.StartDate).days
        if days_running < self.warmup_days:
            # 预热期: 返回空字典 (CointegrationAnalyzer使用default_quota, PairSelector使用默认tier0)
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({days_running}/{self.warmup_days}天), "
                f"所有行业使用默认配额: {self.default_quota}"
            )
            return {}

        # 步骤2: 聚合每个行业的历史数据
        industry_stats = self._aggregate_industry_stats(pairs_manager)

        # 步骤3: 计算每个行业的配额和tier
        industry_quotas = {}
        industry_names = self.algorithm.config.constants['industry_names']

        for industry_code, stats in industry_stats.items():
            weighted_return = stats['realized_pnl'] / stats['realized_cost']
            quota = self._get_quota_by_return(weighted_return)
            tier = self._get_tier_by_return(weighted_return)  # v7.32.0: 新增tier信息

            # v7.32.0: 返回包含tier信息的字典
            industry_quotas[industry_code] = {
                'quota': quota,
                'tier': tier,
                'weighted_return': weighted_return
            }

            # v7.26.0: 详细日志 - 显示非默认配额的计算依据
            # v7.37.1: 添加交易次数, 移除行业代码
            if quota != self.default_quota:
                industry_name = industry_names.get(int(industry_code), f'未知({industry_code})')
                self.algorithm.Debug(
                    f"[行业配额] {industry_name}: "
                    f"累计收益{weighted_return*100:+.2f}% "
                    f"(PnL=${stats['realized_pnl']:,.0f}, Cost=${stats['realized_cost']:,.0f}, 交易{stats['pair_count']}对) "
                    f"→ 配额: {self.default_quota} → {quota} (tier={tier})",
                    level=1  # Debug模式才显示详情
                )

        # v7.13.0: 日志输出 (映射行业代码为中文名)
        # v7.37.1: 删除汇总日志 (已在上方逐个输出详细信息, 避免冗余)
        non_default = {k: v for k, v in industry_quotas.items() if v['quota'] != self.default_quota}
        if not non_default:
            # 只在所有行业都使用默认配额时输出（预热期或无动态调整）
            self.algorithm.Debug(
                f"[行业配额] 本月所有行业使用默认配额: {self.default_quota}"
            )

        return industry_quotas


    def _aggregate_industry_stats(self, pairs_manager) -> Dict[str, Dict]:
        """
        聚合每个行业的历史交易统计

        Args:
            pairs_manager: PairsManager实例

        Returns:
            {
                'industry_code': {
                    'realized_pnl': float,      # 已实现PnL (已平仓交易累计)
                    'realized_cost': float,     # 已实现成本 (已平仓交易累计)
                    'pair_count': int           # 配对数量 (用于调试)
                }
            }

        实现步骤:
        1. 遍历pairs_manager.all_pairs (包含所有状态: COINTEGRATED/LEGACY/ARCHIVED)
        2. 检查pair.industry_code是否存在
        3. 聚合realized_pnl和realized_cost
        4. 只统计至少有1笔历史交易的配对 (trade_count > 0)
        """
        industry_stats = defaultdict(lambda: {
            'realized_pnl': 0.0,
            'realized_cost': 0.0,
            'pair_count': 0
        })

        # 遍历所有配对 (包括COINTEGRATED/LEGACY/ARCHIVED)
        for pair_id, pair in pairs_manager.all_pairs.items():
            # 检查行业代码
            if pair.industry_code is None:
                continue

            # 只统计有交易历史的配对
            if pair.trade_count == 0:
                continue

            # 聚合统计
            industry_code = str(pair.industry_code)
            industry_stats[industry_code]['realized_pnl'] += pair.realized_pnl
            industry_stats[industry_code]['realized_cost'] += pair.realized_cost
            industry_stats[industry_code]['pair_count'] += 1

        return industry_stats


    def _get_quota_by_return(self, weighted_return: float) -> int:
        """
        根据加权收益率计算配额 (v7.35.0: 更保守的5层阶梯)

        Args:
            weighted_return: 加权收益率 (小数, 如0.05表示5%)

        Returns:
            配额数量 (1/2/3/4/5)

        分层逻辑 (v7.35.0 更新阈值和配额):
            weighted_return < 0.00  → tier0 (1个,负收益)
            weighted_return < 0.05  → tier1 (2个,[0%,5%))
            weighted_return < 0.10  → tier2 (3个,[5%,10%))
            weighted_return < 0.15  → tier3 (4个,[10%,15%))
            weighted_return >= 0.15 → tier4 (5个,[15%,∞))
        """
        if weighted_return < self.tier_thresholds['tier0']:
            return self.tier_quotas['tier0']  # <0%
        elif weighted_return < self.tier_thresholds['tier1']:
            return self.tier_quotas['tier1']  # [0%, 5%)
        elif weighted_return < self.tier_thresholds['tier2']:
            return self.tier_quotas['tier2']  # [5%, 10%)
        elif weighted_return < self.tier_thresholds['tier3']:
            return self.tier_quotas['tier3']  # [10%, 15%)
        else:
            return self.tier_quotas['tier4']  # [15%, ∞)


    def _get_tier_by_return(self, weighted_return: float) -> str:
        """
        根据加权收益率计算tier (v7.35.0: 用于Pairs.get_planned_allocation_pct)

        Args:
            weighted_return: 加权收益率 (小数, 如0.05表示5%)

        Returns:
            tier名称 ('tier0'/'tier1'/'tier2'/'tier3'/'tier4')

        分层逻辑 (v7.35.0 更新阈值和max_pct):
            weighted_return < 0.00  → tier0 (max_pct=0.10)
            weighted_return < 0.05  → tier1 (max_pct=0.12)
            weighted_return < 0.10  → tier2 (max_pct=0.15)
            weighted_return < 0.15  → tier3 (max_pct=0.18)
            weighted_return >= 0.15 → tier4 (max_pct=0.20)
        """
        if weighted_return < self.tier_thresholds['tier0']:
            return 'tier0'
        elif weighted_return < self.tier_thresholds['tier1']:
            return 'tier1'
        elif weighted_return < self.tier_thresholds['tier2']:
            return 'tier2'
        elif weighted_return < self.tier_thresholds['tier3']:
            return 'tier3'
        else:
            return 'tier4'
