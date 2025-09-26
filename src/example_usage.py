"""
PairsManager重构后的使用示例
展示优雅的面向对象设计和Pythonic用法
"""

def example_ondata_usage(self, data):
    """
    OnData中的典型使用场景
    展示迭代器接口和集合级操作
    """

    # ========== 1. 全局检查 ==========

    # 检查容量状态
    capacity = self.pairs_manager.get_capacity_status()
    if capacity['is_full']:
        self.Debug(f"[OnData] 已达最大配对数限制 {capacity['current_positions']}/{capacity['max_positions']}")
        return

    # ========== 2. 风险评估 ==========

    # 获取风险汇总（集合级分析）
    risk_summary = self.pairs_manager.get_risk_summary(data)

    # 处理警告
    if risk_summary['warnings']:
        for warning in risk_summary['warnings']:
            self.Debug(f"[风险警告] {warning}")

    # 批量风控平仓（协调操作）
    if risk_summary['statistics']['risk_ratio'] > 0.3:
        closed = self.pairs_manager.close_risky_positions(data, risk_summary)
        self.Debug(f"[风控] 已平仓风险配对: {closed}")

    # ========== 3. 使用迭代器处理配对 ==========

    # 处理活跃配对（可以开仓）
    for pair in self.pairs_manager.active_pairs:
        zscore = pair.get_zscore(data)

        if zscore and abs(zscore) > pair.entry_threshold:
            # 检查全局约束
            if self.pairs_manager.can_open_new_position():
                # 检查行业集中度
                if not self.pairs_manager.is_sector_concentrated(pair.get_sector()):
                    pair.enter_position(self.algorithm, zscore)

        elif zscore and abs(zscore) < pair.exit_threshold:
            if pair.has_position():
                pair.exit_position(self.algorithm, "Mean Reversion")

    # 处理遗留配对（只能平仓）
    for pair in self.pairs_manager.legacy_pairs:
        # Legacy配对只执行风控平仓
        if pair.needs_stop_loss(data):
            pair.exit_position(self.algorithm, "Stop Loss")
        elif pair.is_position_expired():
            pair.exit_position(self.algorithm, "Expired")

    # ========== 4. 列表推导式的优雅用法 ==========

    # 找出需要止损的配对（替代原来的get_pairs_need_stop_loss）
    pairs_need_stop = [p.pair_id for p in self.pairs_manager.tradeable_pairs
                       if p.needs_stop_loss(data)]

    # 找出部分持仓配对（替代原来的get_partial_positions）
    partial_pairs = [p for p in self.pairs_manager.tradeable_pairs
                     if p.get_position_status()['status'] == 'PARTIAL']

    # 找出高质量配对
    high_quality = [p for p in self.pairs_manager.active_pairs
                    if p.quality_score > 0.8]

    # ========== 5. 组合级分析 ==========

    # 获取组合指标
    metrics = self.pairs_manager.get_portfolio_metrics()

    # 日志输出
    self.Debug(
        f"[组合状态] 活跃:{metrics['active_count']}, "
        f"遗留:{metrics['legacy_count']}, "
        f"休眠:{metrics['dormant_count']}, "
        f"暴露度:{metrics.get('exposure_ratio', 0):.2%}"
    )

    # 获取集中度分析
    concentrations = self.pairs_manager.get_concentration_analysis()
    if concentrations and concentrations[0]['is_over_limit']:
        top_pair = concentrations[0]
        self.Debug(
            f"[集中度警告] {top_pair['pair_id']} "
            f"占比{top_pair['percentage']:.1f}%超限"
        )

    # ========== 6. 状态转换 ==========

    # 将已清仓的legacy配对移至dormant
    cleared = self.pairs_manager.transition_legacy_to_dormant()
    if cleared:
        self.Debug(f"[状态转换] {cleared} 已移至休眠")


def example_monthly_rebalance(self):
    """
    月度再平衡示例
    展示批量操作和协调功能
    """

    # 获取容量状态
    capacity = self.pairs_manager.get_capacity_status()

    # 如果利用率过低，寻找新配对
    if capacity['utilization_rate'] < 0.5:
        self.Debug(f"[再平衡] 容量利用率低 {capacity['utilization_rate']:.1%}")
        # 触发新的配对分析...

    # 行业分布检查
    sector_dist = self.pairs_manager.get_sector_concentrations()
    for sector, info in sector_dist.items():
        if info['concentration'] > 0.4:
            self.Debug(f"[再平衡] {sector}行业过度集中 {info['percentage']:.1f}%")
            # 可能需要调整...

    # 清理所有休眠配对的引用（释放内存）
    dormant_count = len(list(self.pairs_manager.dormant_pairs))
    if dormant_count > 50:
        self.Debug(f"[清理] 休眠配对过多({dormant_count})，考虑清理")


def example_emergency_stop(self):
    """
    紧急停止示例
    展示批量平仓功能
    """

    # 市场崩溃或系统故障时
    if self.Portfolio.UnrealizedProfit < -50000:
        # 批量平仓所有持仓
        closed = self.pairs_manager.close_all_positions("Emergency Stop")
        self.Debug(f"[紧急停止] 已平仓{len(closed)}个配对")


# ========== 对比：新旧用法 ==========

# 旧方式（冗余的批量包装）：
# pairs_need_stop = manager.get_pairs_need_stop_loss(data)
# partial_pairs = manager.get_partial_positions()
# over_time = manager.get_over_time_limit_pairs()

# 新方式（优雅的迭代器和列表推导）：
# pairs_need_stop = [p.pair_id for p in manager.tradeable_pairs if p.needs_stop_loss(data)]
# partial_pairs = [p for p in manager.tradeable_pairs if p.get_position_status()['status'] == 'PARTIAL']
# over_time = [p for p in manager.tradeable_pairs if p.is_position_expired()]

# 优势：
# 1. 更Pythonic，符合Python惯用法
# 2. 更灵活，可以自由组合条件
# 3. 更清晰，一眼看出筛选逻辑
# 4. 无需维护大量重复方法