# region imports
from AlgorithmImports import *
from src.Pairs import Pairs
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

        # 风控参数
        self.max_tradeable_pairs = config['max_tradeable_pairs']

        # 主存储：所有曾经出现过的配对
        self.all_pairs = {}  # {pair_id: Pairs对象}

        # 分类索引（只存储pair_id）
        self.active_ids = set()    # 本轮通过协整检验的配对
        self.legacy_ids = set()    # 未通过协整但有持仓的配对
        self.dormant_ids = set()   # 未通过协整且无持仓的配对

        # 统计信息
        self.update_count = 0  # 更新次数（选股轮次）
        self.last_update_time = None  # 上次更新时间


    def create_pairs_from_models(self, modeled_pairs):
        """
        从贝叶斯建模结果创建配对对象
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

            self.algorithm.Debug(f"  创建配对: {pair.pair_id}, 质量分数: {pair.quality_score:.3f}", 2)

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
                status = pair.get_position_status()
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
            if pair.get_position_status() == 'NO POSITION':
                cleared_pairs.append(pair_id)
                self.legacy_ids.remove(pair_id)
                self.dormant_ids.add(pair_id)
                self.algorithm.Debug(f"[PairsManager] {pair_id} 清仓完成，移至dormant", 2)

        return cleared_pairs


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
                if pair.get_position_status() != 'NO POSITION'}


    def get_pairs_without_position(self) -> Dict:
        """
        获取所有无持仓的可交易配对（用于开仓逻辑）
        返回: {pair_id: Pairs对象} 字典
        """
        tradeable_pairs = self.get_all_tradeable_pairs()
        return {pid: pair for pid, pair in tradeable_pairs.items()
                if pair.get_position_status() == 'NO POSITION'}


    def get_all_tradeable_pairs(self) -> Dict:
        """获取所有需要管理的配对字典（保留用于兼容）"""
        tradeable_ids = self.active_ids | self.legacy_ids
        return {pid: self.all_pairs[pid] for pid in tradeable_ids}


    def can_open_new_position(self) -> bool:
        """
        检查是否可以开新仓 - 全局约束检查
        考虑最大配对数限制
        """
        # 统计当前有持仓的配对数（包括完整和部分持仓）
        positions_count = sum(1 for pair in self.tradeable_pairs
                             if pair.get_position_status() != 'NO POSITION')

        can_open = positions_count < self.max_tradeable_pairs

        if not can_open:
            self.algorithm.Debug(
                f"[PairsManager] 达到最大配对限制 {positions_count}/{self.max_tradeable_pairs}", 2
            )

        return can_open


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
        for pair in self.tradeable_pairs:
            info = pair.get_position_info()
            if info['status'] == 'NO POSITION':
                continue

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


    # ========== 3. 迭代器属性 ==========

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