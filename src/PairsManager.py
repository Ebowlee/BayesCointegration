# region imports
from AlgorithmImports import *
from src.Pairs import PositionMode, TradingSignal
from typing import Dict, Set, List
# endregion


class PairsManager:
    """管理整个回测周期内所有配对的生命周期"""

    # ========== 1. 核心管理功能 ==========

    def __init__(self, algorithm, config):
        """
        初始化配对管理器
        Args:
            algorithm: QCAlgorithm实例
            config: 风控配置字典
        """
        self.algorithm = algorithm
        self.config = config  # 保存完整配置，创建Pairs时需要

        # 主存储：所有曾经出现过的配对
        self.all_pairs = {}  # {pair_id: Pairs对象}

        # 分类索引（只存储pair_id）
        self.active_ids = set()    # 本轮通过协整检验的配对
        self.legacy_ids = set()    # 未通过协整但有持仓的配对
        self.dormant_ids = set()   # 未通过协整且无持仓的配对

        # 统计信息
        self.update_count = 0  # 更新次数（选股轮次）
        self.last_update_time = None  # 上次更新时间



    def update_pairs(self, new_pairs_dict: Dict):
        """
        每月选股后更新配对
        new_pairs_dict: {pair_id: Pairs对象} 外部创建的新配对字典
        """
        self.update_count += 1
        self.last_update_time = self.algorithm.Time

        # 记录本轮出现的配对
        current_pair_ids = set(new_pairs_dict.keys())

        # 第一步：处理本轮出现的配对
        for pair_id, new_pair in new_pairs_dict.items():
            if pair_id in self.all_pairs:
                # 已存在的配对：直接传递new_pair对象更新参数
                self.all_pairs[pair_id].update_params(new_pair)
                self.algorithm.Debug(f"[PairsManager] 更新配对 {pair_id}", 2)
            else:
                # 新配对：直接添加
                self.all_pairs[pair_id] = new_pair
                self.algorithm.Debug(f"[PairsManager] 添加新配对 {pair_id}", 2)

        # 第二步：重新分类所有配对
        self.reclassify_pairs(current_pair_ids)

        # 输出统计
        self.log_statistics()


    def reclassify_pairs(self, current_pair_ids: Set):
        """
        重新分类所有配对
        current_pair_ids: 本轮建模成功的pair_id集合（已通过协整检验、质量筛选和贝叶斯建模）
        """
        # 清空分类
        self.active_ids.clear()
        self.legacy_ids.clear()
        self.dormant_ids.clear()

        # 遍历所有配对进行分类
        for pair_id, pair in self.all_pairs.items():
            if pair_id in current_pair_ids:
                # 本轮建模成功的配对
                self.active_ids.add(pair_id)
            else:
                # 本轮未建模成功的配对
                # 有持仓（任何形式）
                if pair.has_position():
                    self.legacy_ids.add(pair_id)
                else:
                    # 无持仓
                    self.dormant_ids.add(pair_id)



    def log_statistics(self):
        """输出统计信息"""
        self.algorithm.Debug(
            f"[PairsManager] 第{self.update_count}轮更新完成: "
            f"活跃={len(self.active_ids)}, "
            f"遗留={len(self.legacy_ids)}, "
            f"休眠={len(self.dormant_ids)}, "
            f"总计={len(self.all_pairs)}", 2
        )


    # ========== 2. 查询与访问功能 ==========

    def has_tradeable_pairs(self) -> bool:
        """检查是否有可交易的配对"""
        return len(self.active_ids | self.legacy_ids) > 0


    def get_pairs_with_position(self) -> Dict:
        """
        获取所有有持仓的可交易配对
        返回: {pair_id: Pairs对象} 字典
        """
        tradeable_pairs = self.get_all_tradeable_pairs()
        return {pid: pair for pid, pair in tradeable_pairs.items()
                if pair.has_position()}


    def get_pairs_without_position(self) -> Dict:
        """
        获取所有无持仓的可交易配对（用于开仓逻辑）
        返回: {pair_id: Pairs对象} 字典
        """
        tradeable_pairs = self.get_all_tradeable_pairs()
        return {pid: pair for pid, pair in tradeable_pairs.items()
                if not pair.has_position()}


    def get_all_tradeable_pairs(self) -> Dict:
        """获取所有需要管理的配对字典（保留用于兼容）"""
        tradeable_ids = self.active_ids | self.legacy_ids
        return {pid: self.all_pairs[pid] for pid in tradeable_ids}


    def get_entry_candidates(self, data) -> List:
        """
        获取所有有开仓信号的配对，按质量分数降序排序
        返回: [(pair, signal, quality_score, planned_pct), ...]
        """
        candidates = []

        # 只检查无持仓的可交易配对
        for pair in self.get_pairs_without_position().values():
            signal = pair.get_signal(data)
            if signal in [TradingSignal.LONG_SPREAD, TradingSignal.SHORT_SPREAD]:
                # 计算计划分配比例
                planned_pct = pair.get_planned_allocation_pct()
                candidates.append((pair, signal, pair.quality_score, planned_pct))

        # 按质量分数降序排序
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates


    def check_concentration_warning(self) -> None:
        """
        软警告：配对数量过多时提醒（非强制限制）
        改为资金自然约束后，这只是一个提醒功能
        """
        positions_count = sum(1 for pair in self.get_all_tradeable_pairs().values()
                             if pair.has_position())

        if positions_count > 15:  # 软上限，只是提醒
            self.algorithm.Debug(
                f"[PairsManager] 注意：配对数量较多({positions_count})，"
                f"建议关注资金分散情况", 2
            )


    def get_sector_concentration(self) -> Dict[str, Dict]:
        """
        获取行业集中度分析
        """
        portfolio = self.algorithm.Portfolio
        total_value = portfolio.TotalPortfolioValue

        if total_value <= 0:
            return {}

        sector_data = {}

        # 遍历所有可交易配对
        for pair in self.get_all_tradeable_pairs().values():
            if not pair.has_position():
                continue
            info = pair.get_position_info()

            sector = pair.sector
            if sector not in sector_data:
                sector_data[sector] = {
                    'value': 0,
                    'pairs': []
                }
            sector_data[sector]['value'] += info['value1'] + info['value2']
            sector_data[sector]['pairs'].append(pair)

        # 计算集中度
        result = {}
        for sector, data in sector_data.items():
            concentration = data['value'] / total_value
            result[sector] = {
                'concentration': concentration,
                'value': data['value'],
                'pairs': data['pairs'],
                'pair_count': len(data['pairs'])
            }

        return result

