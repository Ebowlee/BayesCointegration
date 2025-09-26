# region imports
from AlgorithmImports import *
from src.Pairs import Pairs
from typing import Dict, Set, List
# endregion


class PairsManager:
    """管理整个回测周期内所有配对的生命周期"""

    def __init__(self, algorithm, config):
        """
        初始化配对管理器
        Args:
            algorithm: QCAlgorithm实例
            config: 风控配置字典
        """
        self.algorithm = algorithm
        self.config = config  # 保存完整配置，创建Pairs时需要

        # 风控参数
        self.max_active_pairs = config['max_active_pairs']

        # 主存储：所有曾经出现过的配对
        self.all_pairs = {}  # {pair_id: Pairs对象}

        # 分类索引（只存储pair_id）
        self.active_ids = set()    # 本轮通过协整检验的配对
        self.legacy_ids = set()    # 未通过协整但有持仓的配对
        self.dormant_ids = set()   # 未通过协整且无持仓的配对

        # 统计信息
        self.update_count = 0  # 更新次数（选股轮次）
        self.last_update_time = None  # 上次更新时间


    def create_and_update_pairs(self, modeled_pairs):
        """
        从贝叶斯建模结果创建并更新配对
        整合了原PairsFactory的功能

        Args:
            modeled_pairs: 贝叶斯建模结果列表
        """
        # 创建新配对字典
        new_pairs_dict = {}

        for model_result in modeled_pairs:
            # 准备Pairs构造函数需要的数据（原Factory的_prepare_pair_data）
            pair_data = {
                'symbol1': model_result['symbol1'],
                'symbol2': model_result['symbol2'],
                'sector': model_result['sector'],
                'alpha_mean': model_result['alpha_mean'],
                'beta_mean': model_result['beta_mean'],
                'spread_mean': model_result['residual_mean'],  # residual即为spread
                'spread_std': model_result['residual_std'],     # residual_std即为spread_std
                'quality_score': model_result['quality_score']
            }

            # 创建Pairs对象
            pair = Pairs(self.algorithm, pair_data, self.config)
            new_pairs_dict[pair.pair_id] = pair

            self.algorithm.Debug(f"  创建配对: {pair.pair_id}, 质量分数: {pair.quality_score:.3f}")

        # 调用原有的update_pairs方法进行管理
        self.update_pairs(new_pairs_dict)


    def update_pairs(self, new_pairs_dict: Dict):
        """
        每月选股后更新配对

        Args:
            new_pairs_dict: {pair_id: Pairs对象} 从PairsFactory创建的新配对
        """
        self.update_count += 1
        self.last_update_time = self.algorithm.Time

        # 记录本轮出现的配对
        current_pair_ids = set(new_pairs_dict.keys())

        # 第一步：处理本轮出现的配对
        for pair_id, new_pair in new_pairs_dict.items():
            if pair_id in self.all_pairs:
                # 已存在的配对：更新参数
                # 提取新配对的参数
                model_data = {
                    'symbol1': new_pair.symbol1,
                    'symbol2': new_pair.symbol2,
                    'sector': new_pair.sector,
                    'alpha_mean': new_pair.alpha_mean,
                    'beta_mean': new_pair.beta_mean,
                    'spread_mean': new_pair.spread_mean,
                    'spread_std': new_pair.spread_std,
                    'quality_score': new_pair.quality_score
                }
                self.all_pairs[pair_id].update_params(model_data)
                self.algorithm.Debug(f"[PairsManager] 更新配对 {pair_id}")
            else:
                # 新配对：直接添加
                self.all_pairs[pair_id] = new_pair
                self.algorithm.Debug(f"[PairsManager] 添加新配对 {pair_id}")

        # 第二步：重新分类所有配对
        self._reclassify_pairs(current_pair_ids)

        # 输出统计
        self._log_statistics()


    def _reclassify_pairs(self, current_pair_ids: Set):
        """
        重新分类所有配对

        Args:
            current_pair_ids: 本轮通过协整检验的pair_id集合
        """
        # 清空分类
        self.active_ids.clear()
        self.legacy_ids.clear()
        self.dormant_ids.clear()

        # 遍历所有配对进行分类
        for pair_id, pair in self.all_pairs.items():
            if pair_id in current_pair_ids:
                # 本轮通过协整检验
                self.active_ids.add(pair_id)
            else:
                # 本轮未通过协整检验
                status = pair.get_position_status()['status']
                if status == 'NORMAL' or status == 'PARTIAL':
                    # 有持仓（完整或部分）
                    self.legacy_ids.add(pair_id)
                else:
                    # 无持仓
                    self.dormant_ids.add(pair_id)


    def transition_legacy_to_dormant(self):
        """
        状态转换：将已清仓的legacy配对移至dormant
        返回已转换的配对ID列表
        """
        cleared_pairs = []

        for pair_id in list(self.legacy_ids):  # 使用list()避免迭代时修改
            pair = self.all_pairs[pair_id]
            if pair.get_position_status()['status'] == 'NO POSITION':
                cleared_pairs.append(pair_id)
                self.legacy_ids.remove(pair_id)
                self.dormant_ids.add(pair_id)
                self.algorithm.Debug(f"[PairsManager] {pair_id} 清仓完成，移至dormant")

        return cleared_pairs


    # ========== 迭代器接口 ==========

    @property
    def active_pairs(self):
        """活跃配对生成器（可以开仓）"""
        for pair_id in self.active_ids:
            yield self.all_pairs[pair_id]

    @property
    def legacy_pairs(self):
        """遗留配对生成器（只能平仓）"""
        for pair_id in self.legacy_ids:
            yield self.all_pairs[pair_id]

    @property
    def tradeable_pairs(self):
        """所有可交易配对生成器（active + legacy）"""
        for pair_id in (self.active_ids | self.legacy_ids):
            yield self.all_pairs[pair_id]

    @property
    def dormant_pairs(self):
        """休眠配对生成器（仅供查询）"""
        for pair_id in self.dormant_ids:
            yield self.all_pairs[pair_id]

    @property
    def all_pairs_iter(self):
        """所有配对的生成器"""
        for pair in self.all_pairs.values():
            yield pair

    def has_tradeable_pairs(self) -> bool:
        """检查是否有可交易的配对"""
        return len(self.active_ids | self.legacy_ids) > 0

    def get_all_tradeable_pairs(self) -> Dict:
        """获取所有需要管理的配对字典（保留用于兼容）"""
        tradeable_ids = self.active_ids | self.legacy_ids
        return {pid: self.all_pairs[pid] for pid in tradeable_ids}


    # ========== 集合级分析方法（真正的Manager职责）==========

    def get_portfolio_metrics(self):
        """
        计算组合级指标 - 提供全面的组合状态概览
        """
        metrics = {
            'total_pairs': len(self.all_pairs),
            'active_count': len(self.active_ids),
            'legacy_count': len(self.legacy_ids),
            'dormant_count': len(self.dormant_ids),
            'positions_with_value': 0,
            'total_exposure': 0,
            'average_quality_score': 0,
            'sector_distribution': {},
            'status_breakdown': {'NORMAL': 0, 'PARTIAL': 0, 'NO POSITION': 0}
        }

        quality_scores = []

        for pair in self.tradeable_pairs:
            # 统计持仓状态
            status = pair.get_position_status()['status']
            metrics['status_breakdown'][status] += 1

            # 统计持仓价值
            value = pair.get_position_value()
            if value > 0:
                metrics['positions_with_value'] += 1
                metrics['total_exposure'] += value

                # 行业分布
                sector = pair.get_sector()
                metrics['sector_distribution'][sector] = \
                    metrics['sector_distribution'].get(sector, 0) + value

            # 收集质量分数
            quality_scores.append(pair.quality_score)

        # 计算平均质量分数
        if quality_scores:
            metrics['average_quality_score'] = sum(quality_scores) / len(quality_scores)

        # 将行业分布转换为百分比
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        if portfolio_value > 0:
            metrics['sector_distribution'] = {
                sector: value / portfolio_value
                for sector, value in metrics['sector_distribution'].items()
            }
            metrics['exposure_ratio'] = metrics['total_exposure'] / portfolio_value

        return metrics

    def get_concentration_analysis(self):
        """
        集中度分析 - 识别风险集中点
        返回按集中度排序的配对列表，包含是否超限标记
        """
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        if portfolio_value <= 0:
            return []

        concentrations = []
        max_concentration = self.config.get('max_pair_concentration', 0.25)

        for pair in self.tradeable_pairs:
            value = pair.get_position_value()
            if value > 0:
                concentration = value / portfolio_value
                concentrations.append({
                    'pair_id': pair.pair_id,
                    'sector': pair.get_sector(),
                    'value': value,
                    'concentration': concentration,
                    'percentage': concentration * 100,
                    'is_over_limit': concentration > max_concentration,
                    'quality_score': pair.quality_score
                })

        # 按集中度降序排序
        return sorted(concentrations, key=lambda x: x['concentration'], reverse=True)

    def get_risk_summary(self, data):
        """
        风险汇总报告 - 一站式风险评估
        提供多维度的风险指标和预警
        """
        summary = {
            'timestamp': self.algorithm.Time,
            'pairs_at_risk': {},
            'statistics': {},
            'warnings': []
        }

        # 收集各类风险配对
        stop_loss_pairs = []
        expired_pairs = []
        partial_pairs = []
        high_zscore_pairs = []

        for pair in self.tradeable_pairs:
            pair_id = pair.pair_id

            # 止损检查
            if pair.needs_stop_loss(data):
                stop_loss_pairs.append(pair_id)

            # 持仓时间检查
            if pair.is_position_expired():
                expired_pairs.append(pair_id)

            # 部分持仓检查
            if pair.get_position_status()['status'] == 'PARTIAL':
                partial_pairs.append(pair_id)

            # 极端z-score检查
            zscore = pair.get_zscore(data)
            if zscore and abs(zscore) > 2.5:
                high_zscore_pairs.append((pair_id, zscore))

        # 汇总风险配对
        summary['pairs_at_risk'] = {
            'stop_loss_triggered': stop_loss_pairs,
            'holding_expired': expired_pairs,
            'partial_positions': partial_pairs,
            'extreme_zscore': high_zscore_pairs
        }

        # 统计信息
        summary['statistics'] = {
            'total_risk_count': len(stop_loss_pairs) + len(expired_pairs) + len(partial_pairs),
            'risk_ratio': 0
        }

        # 计算风险比例
        total_tradeable = len(self.active_ids | self.legacy_ids)
        if total_tradeable > 0:
            summary['statistics']['risk_ratio'] = \
                summary['statistics']['total_risk_count'] / total_tradeable

        # 生成警告信息
        if len(stop_loss_pairs) > 2:
            summary['warnings'].append(f"多个配对触发止损({len(stop_loss_pairs)}个)")

        if len(partial_pairs) > 0:
            summary['warnings'].append(f"存在部分持仓(腿断)配对: {partial_pairs}")

        if summary['statistics']['risk_ratio'] > 0.5:
            summary['warnings'].append("超过50%的配对存在风险")

        return summary

    def get_sector_concentrations(self) -> Dict[str, float]:
        """
        获取行业集中度分析
        返回各行业占总资产的比例
        """
        sector_values = {}

        for pair in self.tradeable_pairs:
            value = pair.get_position_value()
            if value > 0:
                sector = pair.get_sector()
                sector_values[sector] = sector_values.get(sector, 0) + value

        # 计算占比
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        if portfolio_value > 0:
            return {
                sector: {
                    'value': value,
                    'percentage': (value / portfolio_value) * 100,
                    'concentration': value / portfolio_value
                }
                for sector, value in sector_values.items()
            }

        return {}


    # ========== 全局约束和协调方法 ==========

    def can_open_new_position(self) -> bool:
        """
        检查是否可以开新仓 - 全局约束检查
        考虑最大配对数限制
        """
        # 统计当前有持仓的配对数
        positions_count = sum(1 for pair in self.tradeable_pairs
                             if pair.get_position_status()['status'] == 'NORMAL')

        can_open = positions_count < self.max_active_pairs

        if not can_open:
            self.algorithm.Debug(
                f"[PairsManager] 达到最大配对限制 {positions_count}/{self.max_active_pairs}"
            )

        return can_open

    def is_sector_concentrated(self, sector: str, threshold: float = 0.30) -> bool:
        """
        检查特定行业是否过度集中
        用于新开仓前的风控检查
        """
        concentrations = self.get_sector_concentrations()
        if sector in concentrations:
            return concentrations[sector]['concentration'] > threshold
        return False

    def close_all_positions(self, reason: str = "Manual Close All"):
        """
        批量平仓所有持仓 - 合理的批量操作
        用于紧急风控或策略终止
        """
        closed_pairs = []

        for pair in self.tradeable_pairs:
            if pair.get_position_status()['status'] != 'NO POSITION':
                # 调用Pairs的平仓方法
                success = pair.exit_position(self.algorithm, reason)
                if success:
                    closed_pairs.append(pair.pair_id)
                    self.algorithm.Debug(f"[PairsManager] 平仓 {pair.pair_id}: {reason}")

        if closed_pairs:
            self.algorithm.Debug(
                f"[PairsManager] 批量平仓完成，共{len(closed_pairs)}个配对: {closed_pairs}"
            )

        return closed_pairs

    def close_risky_positions(self, data, risk_summary=None):
        """
        平仓所有风险配对 - 智能批量风控
        基于风险汇总报告执行
        """
        if risk_summary is None:
            risk_summary = self.get_risk_summary(data)

        closed = {
            'stop_loss': [],
            'expired': [],
            'partial': []
        }

        for pair in self.tradeable_pairs:
            pair_id = pair.pair_id

            # 止损平仓
            if pair_id in risk_summary['pairs_at_risk']['stop_loss_triggered']:
                if pair.exit_position(self.algorithm, "Stop Loss"):
                    closed['stop_loss'].append(pair_id)

            # 超时平仓
            elif pair_id in risk_summary['pairs_at_risk']['holding_expired']:
                if pair.exit_position(self.algorithm, "Holding Expired"):
                    closed['expired'].append(pair_id)

            # 部分持仓修复
            elif pair_id in risk_summary['pairs_at_risk']['partial_positions']:
                if pair.exit_position(self.algorithm, "Partial Position Cleanup"):
                    closed['partial'].append(pair_id)

        # 汇总日志
        total_closed = len(closed['stop_loss']) + len(closed['expired']) + len(closed['partial'])
        if total_closed > 0:
            self.algorithm.Debug(
                f"[PairsManager] 风控平仓: 止损{len(closed['stop_loss'])}个, "
                f"超时{len(closed['expired'])}个, 腿断{len(closed['partial'])}个"
            )

        return closed

    def get_capacity_status(self):
        """
        获取容量状态 - 用于判断是否可以继续开仓
        """
        positions_count = sum(1 for pair in self.tradeable_pairs
                             if pair.get_position_status()['status'] == 'NORMAL')

        return {
            'current_positions': positions_count,
            'max_positions': self.max_active_pairs,
            'available_slots': self.max_active_pairs - positions_count,
            'utilization_rate': positions_count / self.max_active_pairs if self.max_active_pairs > 0 else 0,
            'is_full': positions_count >= self.max_active_pairs
        }




    def _log_statistics(self):
        """输出统计信息"""
        self.algorithm.Debug(
            f"[PairsManager] 第{self.update_count}轮更新完成: "
            f"活跃={len(self.active_ids)}, "
            f"遗留={len(self.legacy_ids)}, "
            f"休眠={len(self.dormant_ids)}, "
            f"总计={len(self.all_pairs)}"
        )