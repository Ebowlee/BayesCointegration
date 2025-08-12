# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple, Set
# endregion


class CentralPairManager:
    """
    中央配对管理器 - v0最小交互版本
    
    仅处理Alpha在选股日提交的活跃配对，维护当前活跃目录。
    不在此阶段写交易历史。
    """
    
    def __init__(self, algorithm, config=None):
        """初始化CPM"""
        self.algorithm = algorithm
        
        # 核心容器
        self.current_active = {}  # {PairKey: {cycle_id, beta, quality_score, eligible_in_cycle}}
        self.history_log = []     # 本阶段不写入
        
        # 幂等性控制
        self.last_cycle_id = None
        self.last_cycle_pairs = set()  # 存储pair_key集合用于比较
    
    def submit_modeled_pairs(self, cycle_id: int, pairs: List[Dict]) -> None:
        """
        接收Alpha模型提交的当轮活跃配对
        
        Args:
            cycle_id: 轮次标识 (yyyymmdd格式)
            pairs: 配对列表，每项包含 {symbol1_value, symbol2_value, beta, quality_score}
        """
        # 1. 批内去重校验
        seen_keys = set()
        for pair in pairs:
            pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
            if pair_key in seen_keys:
                self.algorithm.Error(f"[CPM] 批内重复配对: {pair_key}")
                raise ValueError(f"Duplicate pair_key in batch: {pair_key}")
            seen_keys.add(pair_key)
        
        # 2. 幂等性处理（严格冻结）
        new_keys = {self._make_pair_key(p['symbol1_value'], p['symbol2_value']) 
                    for p in pairs}
        
        if cycle_id == self.last_cycle_id:
            # 同cycle重复提交 - 严格冻结，拒绝任何修改
            if new_keys != self.last_cycle_pairs:
                # 集合不同，拒绝
                self.algorithm.Error(f"[CPM] 拒绝修改已冻结的cycle {cycle_id}")
                return
            else:
                # 集合相同，检查参数是否相同
                for pair in pairs:
                    pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
                    existing = self.current_active.get(pair_key)
                    if existing and (existing['beta'] != pair['beta'] or 
                                    existing['quality_score'] != pair['quality_score']):
                        self.algorithm.Error(f"[CPM] 拒绝修改已冻结的cycle {cycle_id} 参数")
                        return
                
                # 完全相同，幂等返回
                self.algorithm.Debug(f"[CPM] Cycle {cycle_id} 幂等重复提交，忽略")
                return
        
        # 3. 清理旧配对（仅在新cycle时）
        if self.last_cycle_id is not None:
            to_remove = []
            for pair_key in self.current_active:
                if pair_key not in new_keys:
                    if self._has_open_instance(pair_key):
                        # 跨期持仓，保留但标记不活跃
                        self.current_active[pair_key]['eligible_in_cycle'] = False
                        self.algorithm.Debug(f"[CPM] 保留跨期持仓: {pair_key}")
                    else:
                        # 无持仓，删除
                        to_remove.append(pair_key)
            
            for key in to_remove:
                del self.current_active[key]
                self.algorithm.Debug(f"[CPM] 删除无持仓配对: {key}")
        
        # 4. Upsert新配对
        for pair in pairs:
            pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
            self.current_active[pair_key] = {
                'cycle_id': cycle_id,
                'beta': pair['beta'],
                'quality_score': pair['quality_score'],
                'eligible_in_cycle': True
            }
        
        # 5. 更新状态
        self.last_cycle_id = cycle_id
        self.last_cycle_pairs = new_keys
        
        self.algorithm.Debug(f"[CPM] Cycle {cycle_id} 提交完成: {len(pairs)}个配对")
    
    def _make_pair_key(self, symbol1_value: str, symbol2_value: str) -> Tuple[str, str]:
        """生成规范化的pair_key"""
        return tuple(sorted([symbol1_value, symbol2_value]))
    
    def _has_open_instance(self, pair_key: Tuple[str, str]) -> bool:
        """检查配对是否有未平仓实例（占位，暂返回False）"""
        # TODO: 后续接入Execution时实现真实逻辑
        return False
    
    def get_current_active(self) -> Dict:
        """获取当前活跃配对（供查询）"""
        return self.current_active.copy()