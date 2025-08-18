# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple, Set
from datetime import timedelta
# endregion


class CentralPairManager:
    """
    中央配对管理器 - v1版本（Alpha交互 + PC意图管理）
    
    v0功能：处理Alpha在选股日提交的活跃配对
    v1新增：处理PC提交的交易意图，管理运行期实例
    """
    
    def __init__(self, algorithm, config=None):
        """初始化CPM"""
        self.algorithm = algorithm
        
        # === 核心数据结构 ===
        self.current_pairs = {}        # 本轮活跃配对 {PairKey: {cycle_id, beta, quality_score}}
        self.legacy_pairs = {}         # 遗留持仓配对（上轮的但仍有持仓）
        self.retired_pairs = {}        # 已退休配对（最近N天内平仓的）
        
        # === 运行期管理 ===
        self.open_instances = {}       # 运行期实例（仅未平）
        self.closed_instances = {}     # 历史实例（用于统计）
        self.instance_counters = {}    # 实例计数器（永不回退）
        
        # === 状态记录 ===
        self.history_log = []          # 历史日志（预留）
    
    # ==================== Alpha交互 ====================
    def submit_modeled_pairs(self, cycle_id: int, pairs: List[Dict]) -> None:
        """
        接收Alpha模型提交的当轮活跃配对
        
        Args:
            cycle_id: 轮次标识 (yyyymmdd格式)
            pairs: 配对列表，每项包含 {symbol1_value, symbol2_value, beta, quality_score}
        """
        # 1. 批内去重校验（保留这个有价值的检查）
        seen_keys = set()
        valid_pairs = []  # 只处理有效的配对
        for pair in pairs:
            pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
            if pair_key in seen_keys:
                self.algorithm.Error(f"[CPM] 批内重复配对，跳过: {pair_key}")
                continue  # 跳过重复项继续处理
            seen_keys.add(pair_key)
            valid_pairs.append(pair)
        
        # 2. 处理配对迁移
        new_keys = {self._make_pair_key(p['symbol1_value'], p['symbol2_value']) 
                    for p in valid_pairs}
        
        # 2.1 迁移旧的current_pairs
        for pair_key in list(self.current_pairs.keys()):
            if pair_key not in new_keys:
                if self._has_open_instance(pair_key):
                    # 有持仓：迁移到legacy_pairs
                    self.legacy_pairs[pair_key] = self.current_pairs[pair_key]
                    self.algorithm.Debug(f"[CPM] 配对迁移到legacy: {pair_key}")
                else:
                    # 无持仓：可能迁移到retired_pairs
                    if pair_key in self.closed_instances:
                        self._retire_pair(pair_key)
                    self.algorithm.Debug(f"[CPM] 删除无持仓配对: {pair_key}")
                # 从current_pairs移除
                del self.current_pairs[pair_key]
        
        # 2.2 清理已平仓的legacy_pairs
        for pair_key in list(self.legacy_pairs.keys()):
            if not self._has_open_instance(pair_key):
                self._retire_pair(pair_key)
                del self.legacy_pairs[pair_key]
                self.algorithm.Debug(f"[CPM] legacy配对已平仓，移至retired: {pair_key}")
        
        # 3. 更新current_pairs（仅包含本轮活跃配对）
        self.current_pairs.clear()  # 清空后重建
        for pair in valid_pairs:
            pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
            self.current_pairs[pair_key] = {
                'cycle_id': cycle_id,
                'beta': pair['beta'],
                'quality_score': pair['quality_score']
            }
        
        self.algorithm.Debug(f"[CPM] Cycle {cycle_id} 提交完成: {len(self.current_pairs)}个配对")
    
    def _make_pair_key(self, symbol1_value: str, symbol2_value: str) -> Tuple[str, str]:
        """生成规范化的pair_key"""
        return tuple(sorted([symbol1_value, symbol2_value]))
    
    def _has_open_instance(self, pair_key: Tuple[str, str]) -> bool:
        """检查配对是否有未平仓实例（v1实现）"""
        return pair_key in self.open_instances
    
    def _retire_pair(self, pair_key: Tuple[str, str]) -> None:
        """将配对移至retired_pairs"""
        if pair_key in self.closed_instances:
            exit_time = self.closed_instances[pair_key].get('exit_time')
            self.retired_pairs[pair_key] = {
                'retired_time': exit_time or self.algorithm.Time,
                'cycle_id': self.closed_instances[pair_key].get('cycle_id')
            }
    
    def get_current_pairs(self) -> Dict[Tuple[str, str], Dict]:
        """
        获取本轮活跃配对
        
        Returns:
            Dict: current_pairs的副本，包含cycle_id, beta, quality_score
        """
        return self.current_pairs.copy()
    
    def get_legacy_pairs(self) -> Dict[Tuple[str, str], Dict]:
        """
        获取遗留持仓配对
        
        Returns:
            Dict: legacy_pairs的副本，包含cycle_id, beta, quality_score
        """
        return self.legacy_pairs.copy()
    
    def get_retired_pairs(self) -> Dict[Tuple[str, str], Dict]:
        """
        获取已退休配对
        
        Returns:
            Dict: retired_pairs的副本，包含retired_time, cycle_id等
        """
        return self.retired_pairs.copy()
    
    def get_pairs_summary(self) -> Dict:
        """
        获取配对统计摘要
        
        Returns:
            Dict: 各状态配对的数量统计
        """
        return {
            'current_count': len(self.current_pairs),
            'legacy_count': len(self.legacy_pairs),
            'retired_count': len(self.retired_pairs),
            'open_instances_count': len(self.open_instances),
            'closed_instances_count': len(self.closed_instances),
            'total_tracked': len(self.current_pairs) + len(self.legacy_pairs)
        }
    
    # ==================== 风控查询接口 ====================
    def get_risk_alerts(self) -> Dict:
        """
        供RiskManagement查询的统一接口
        
        Returns:
            Dict: 包含各类风控警报信息
                - expired_pairs: 过期配对列表（从legacy_pairs中识别）
                - long_holding_pairs: 长期持仓配对（未来实现）
                - single_leg_instances: 单腿实例（未来实现）
        """
        # 动态生成过期配对列表：legacy_pairs中没有持仓的配对
        expired_pairs = []
        for pair_key in list(self.legacy_pairs.keys()):
            if not self._has_open_instance(pair_key):
                expired_pairs.append({
                    'pair_key': pair_key,
                    'reason': 'expired_no_position'
                })
        
        alerts = {
            'expired_pairs': expired_pairs,
        }
        
        # 未来可以添加更多风控信息
        # alerts['long_holding_pairs'] = self._get_long_holding_pairs()
        # alerts['single_leg_instances'] = self._get_incomplete_instances()
        
        return alerts
    
    def clear_expired_pairs(self):
        """清理过期配对（RiskManagement处理完后调用）"""
        # 清理已平仓的legacy_pairs
        for pair_key in list(self.legacy_pairs.keys()):
            if not self._has_open_instance(pair_key):
                self._retire_pair(pair_key)
                del self.legacy_pairs[pair_key]
                self.algorithm.Debug(f"[CPM] 清理过期配对: {pair_key}")
    
    def get_active_pairs_with_position(self) -> List[Dict]:
        """
        获取有持仓的活跃配对
        
        Returns:
            List[Dict]: 活跃配对列表，每项包含：
                - pair_key: 配对键
                - beta: 对冲比率
                - quality_score: 质量分数
                - instance_id: 实例ID
                - cycle_id: 所属周期
        """
        active_pairs = []
        
        # 合并current_pairs和legacy_pairs中有持仓的配对
        all_pairs = {**self.current_pairs, **self.legacy_pairs}
        
        for pair_key, info in all_pairs.items():
            # 只返回有持仓的配对
            if pair_key in self.open_instances:
                active_pairs.append({
                    'pair_key': pair_key,
                    'beta': info.get('beta', 1.0),
                    'quality_score': info.get('quality_score', 0.5),
                    'instance_id': self.open_instances[pair_key].get('instance_id'),
                    'cycle_id': info.get('cycle_id')
                })
        
        return active_pairs
    
    def get_pairs_with_holding_info(self) -> List[Dict]:
        """
        获取配对的持仓信息，包括持仓时间
        
        Returns:
            List[Dict]: 配对信息列表，每项包含：
                - pair_key: 配对键
                - holding_days: 持仓天数
                - instance_info: 实例信息
        
        注意：只返回有 entry_time 的配对（需要 Execution 模块填充）
        """
        pairs_info = []
        current_time = self.algorithm.Time
        
        for pair_key, instance in self.open_instances.items():
            # 严格使用 entry_time（未来由 Execution 模块填充）
            entry_time = instance.get('entry_time')
            if entry_time:  # 只有当 entry_time 存在时才计算
                holding_days = (current_time.date() - entry_time.date()).days
                pairs_info.append({
                    'pair_key': pair_key,
                    'holding_days': holding_days,
                    'instance_info': instance
                })
        
        return pairs_info
    
    # ==================== OOE接口 ====================
    def on_pair_entry_complete(self, pair_key: Tuple[str, str], entry_time) -> bool:
        """
        标记配对入场完成
        
        Args:
            pair_key: 配对键
            entry_time: 入场时间
            
        Returns:
            bool: 是否成功更新
        """
        # 规范化pair_key
        if isinstance(pair_key, tuple) and len(pair_key) == 2:
            pair_key = self._make_pair_key(str(pair_key[0]), str(pair_key[1]))
        else:
            self.algorithm.Error(f"[CPM] on_pair_entry_complete: 无效的pair_key格式: {pair_key}")
            return False
        
        # 更新open_instances中的entry_time
        if pair_key in self.open_instances:
            self.open_instances[pair_key]['entry_time'] = entry_time
            self.open_instances[pair_key]['last_exec_state'] = 'entry_completed'
            self.algorithm.Debug(f"[CPM] 配对入场完成: {pair_key} at {entry_time}")
            return True
        else:
            self.algorithm.Debug(f"[CPM] on_pair_entry_complete: {pair_key} 不在open_instances中")
            return False
    
    def on_pair_exit_complete(self, pair_key: Tuple[str, str], exit_time) -> bool:
        """
        标记配对出场完成
        
        Args:
            pair_key: 配对键
            exit_time: 出场时间
            
        Returns:
            bool: 是否成功处理
        """
        # 规范化pair_key
        if isinstance(pair_key, tuple) and len(pair_key) == 2:
            pair_key = self._make_pair_key(str(pair_key[0]), str(pair_key[1]))
        else:
            self.algorithm.Error(f"[CPM] on_pair_exit_complete: 无效的pair_key格式: {pair_key}")
            return False
        
        # 处理平仓完成
        if pair_key in self.open_instances:
            instance = self.open_instances[pair_key]
            
            # 计算持仓天数
            entry_time = instance.get('entry_time')
            holding_days = None
            if entry_time and exit_time:
                holding_days = (exit_time.date() - entry_time.date()).days
            
            # 移到closed_instances
            self.closed_instances[pair_key] = {
                'instance_id': instance.get('instance_id'),
                'cycle_id': instance.get('cycle_id_start'),
                'entry_time': entry_time,
                'exit_time': exit_time,
                'holding_days': holding_days,
                'last_exec_state': 'exit_completed'
            }
            
            # 从open_instances删除
            del self.open_instances[pair_key]
            
            # 从legacy_pairs中移除（如果存在）
            if pair_key in self.legacy_pairs:
                # 遗留配对平仓后，移至retired_pairs
                self._retire_pair(pair_key)
                del self.legacy_pairs[pair_key]
                self.algorithm.Debug(f"[CPM] 移除遗留配对: {pair_key}")
            
            self.algorithm.Debug(
                f"[CPM] 配对出场完成: {pair_key} at {exit_time}, "
                f"持仓{holding_days}天"
            )
            return True
        else:
            self.algorithm.Debug(f"[CPM] on_pair_exit_complete: {pair_key} 不在open_instances中")
            return False
    
    def get_all_active_pairs(self) -> Set[Tuple[str, str]]:
        """
        获取所有活跃配对键
        
        Returns:
            Set[Tuple[str, str]]: 所有在open_instances中的配对键
        """
        return set(self.open_instances.keys())
    
    def get_pair_state(self, pair_key: Tuple[str, str]) -> str:
        """
        获取配对状态
        
        Args:
            pair_key: 配对键
            
        Returns:
            str: 'open' | 'closed' | 'unknown'
        """
        # 规范化pair_key
        if isinstance(pair_key, tuple) and len(pair_key) == 2:
            pair_key = self._make_pair_key(str(pair_key[0]), str(pair_key[1]))
        
        if pair_key in self.open_instances:
            return 'open'
        elif pair_key in self.closed_instances:
            return 'closed'
        else:
            return 'unknown'
    
    # ==================== Alpha查询接口 ====================
    def get_trading_pairs(self) -> Set[Tuple[str, str]]:
        """
        获取所有正在持仓的配对
        
        Returns:
            Set[Tuple[str, str]]: 所有在open_instances中的配对键集合
        """
        return set(self.open_instances.keys())
    
    def get_recent_closed_pairs(self, days: int = 7) -> Set[Tuple[str, str]]:
        """
        获取最近N天内平仓的配对（用于冷却期控制）
        
        Args:
            days: 冷却期天数，默认7天
            
        Returns:
            Set[Tuple[str, str]]: 最近days天内平仓的配对集合
        """
        if not self.closed_instances:
            return set()
        
        current_time = self.algorithm.Time
        cutoff_time = current_time - timedelta(days=days)
        recent_closed = set()
        
        for pair_key, info in self.closed_instances.items():
            exit_time = info.get('exit_time')
            if exit_time and exit_time > cutoff_time:
                recent_closed.add(pair_key)
        
        self.algorithm.Debug(f"[CPM] 冷却期配对({days}天): {len(recent_closed)}个")
        return recent_closed
    
    def get_excluded_pairs(self, cooldown_days: int = 7) -> Set[Tuple[str, str]]:
        """
        获取Alpha选股时应该排除的所有配对
        
        这是一个统一接口，返回所有不应该被选择的配对：
        1. 正在持仓的配对（避免重复建仓）
        2. 最近平仓的配对（冷却期机制）
        
        Args:
            cooldown_days: 冷却期天数，默认7天
            
        Returns:
            Set[Tuple[str, str]]: 应该被排除的配对集合
        """
        trading = self.get_trading_pairs()
        recent_closed = self.get_recent_closed_pairs(cooldown_days)
        excluded = trading | recent_closed
        
        if excluded:
            self.algorithm.Debug(
                f"[CPM] 排除配对总计: {len(excluded)}个 "
                f"(持仓{len(trading)}个, 冷却期{len(recent_closed)}个)"
            )
        
        return excluded