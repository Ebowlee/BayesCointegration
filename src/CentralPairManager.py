# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple, Set
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
        
        # === v0 容器 ===
        self.current_active = {}       # {PairKey: {cycle_id, beta, quality_score, eligible_in_cycle}}
        self.history_log = []          # 本阶段不写入
        self.last_cycle_id = None
        self.last_cycle_pairs = set()
        
        # === v1 新增容器 ===
        self.intents_log = []          # 意图日志
        self.open_instances = {}       # 运行期实例（仅未平）
        self.instance_counters = {}    # 实例计数器（永不回退）
        self.daily_intent_cache = {}   # 日去重缓存
        self.cache_date = None         # 缓存日期
        
        # === 风控支持 ===
        self.expired_pairs = []        # 过期配对列表
    
    # === v0 Alpha交互（保持不变）===
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
        
        # 3. 清理旧配对并识别过期配对（仅在新cycle时）
        self.expired_pairs = []  # 重置过期配对列表
        if self.last_cycle_id is not None:
            to_remove = []
            for pair_key in self.current_active:
                if pair_key not in new_keys:
                    if self._has_open_instance(pair_key):
                        # 跨期持仓，保留但标记不活跃，加入过期列表
                        self.current_active[pair_key]['eligible_in_cycle'] = False
                        self.expired_pairs.append({
                            'pair_key': pair_key,
                            'cycle_id': self.current_active[pair_key]['cycle_id'],
                            'reason': 'expired'
                        })
                        self.algorithm.Debug(f"[CPM] 识别过期配对(有持仓): {pair_key}")
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
        """检查配对是否有未平仓实例（v1实现）"""
        return pair_key in self.open_instances
    
    def get_current_active(self) -> Dict:
        """获取当前活跃配对（供查询）"""
        return self.current_active.copy()
    
    # === v1 PC交互 ===
    def submit_intent(self, pair_key: Tuple[str, str], action: str, intent_date: int) -> str:
        """
        接收PC提交的交易意图
        
        Args:
            pair_key: 配对键
            action: "prepare_open" 或 "prepare_close"
            intent_date: 意图日期（yyyymmdd）
            
        Returns:
            str: "accepted" | "ignored_duplicate" | "ignored_no_position" | "rejected"
        """
        # 1. 使用统一的键规范化
        if isinstance(pair_key, tuple) and len(pair_key) == 2:
            pair_key = self._make_pair_key(str(pair_key[0]), str(pair_key[1]))
        else:
            self.algorithm.Error(f"[CPM] 拒绝(INVALID_KEY): 无效的pair_key格式: {pair_key}")
            return "rejected"
        
        # 2. 参数验证
        if action not in ["prepare_open", "prepare_close"]:
            self.algorithm.Error(f"[CPM] 拒绝(INVALID_ACTION): 无效action: {action}")
            return "rejected"
        
        # 3. 缓存日期检查与清理
        if self.cache_date != intent_date:
            self.daily_intent_cache.clear()
            self.cache_date = intent_date
            self.algorithm.Debug(f"[CPM] 清理日缓存，新日期: {intent_date}")
        
        # 4. 同日去重检查
        cache_key = (pair_key, intent_date)
        if cache_key in self.daily_intent_cache:
            existing_action = self.daily_intent_cache[cache_key]
            if existing_action == action:
                self.algorithm.Debug(f"[CPM] 忽略重复意图(ignored_duplicate): {pair_key} {action} on {intent_date}")
                return "ignored_duplicate"
            else:
                self.algorithm.Error(f"[CPM] 拒绝(CONFLICT_SAME_DAY): {pair_key} 已有{existing_action}，又收到{action}")
                return "rejected"
        
        # 5. 根据action类型获取cycle_id和instance_id
        cycle_id = None
        instance_id = None
        
        if action == "prepare_open":
            # 开仓：从current_active获取cycle_id
            if pair_key in self.current_active:
                cycle_id = self.current_active[pair_key].get('cycle_id')
            else:
                self.algorithm.Error(f"[CPM] 拒绝(NOT_ACTIVE): {pair_key} 不在当前活跃列表")
                return "rejected"
            
            # 检查开仓资格
            eligibility_check = self._check_open_eligibility(pair_key)
            if not eligibility_check['eligible']:
                self.algorithm.Error(f"[CPM] 拒绝({eligibility_check['code']}): {pair_key} - {eligibility_check['reason']}")
                return "rejected"
            
            # 创建open_instance并获取instance_id
            instance_id = self._create_open_instance(pair_key, cycle_id, intent_date)
            
        elif action == "prepare_close":
            # 平仓：从open_instances获取cycle_id和instance_id
            if pair_key in self.open_instances:
                instance_info = self.open_instances[pair_key]
                cycle_id = instance_info['cycle_id_start']
                instance_id = instance_info['instance_id']
            else:
                self.algorithm.Debug(f"[CPM] 忽略平仓(ignored_no_position): {pair_key} 无仓可平")
                return "ignored_no_position"
        
        # 6. 记录意图
        intent_record = {
            'pair_key': pair_key,
            'action': action,
            'cycle_id': cycle_id,
            'intent_date': intent_date,
            'instance_id': instance_id,
            'fulfilled': False,
            'timestamp': self.algorithm.Time
        }
        self.intents_log.append(intent_record)
        
        # 7. 更新缓存
        self.daily_intent_cache[cache_key] = action
        
        self.algorithm.Debug(
            f"[CPM] 接受意图: {pair_key} {action} for {intent_date} "
            f"(cycle={cycle_id}, instance={instance_id})"
        )
        return "accepted"
    
    def _check_open_eligibility(self, pair_key: Tuple[str, str]) -> Dict:
        """检查开仓资格（四个条件）"""
        # 条件1: pair_key在current_active中（已在上层检查）
        if pair_key not in self.current_active:
            return {'eligible': False, 'reason': '配对不在当前活跃列表', 'code': 'NOT_ACTIVE'}
        
        pair_info = self.current_active[pair_key]
        
        # 条件2: eligible_in_cycle == True
        if not pair_info.get('eligible_in_cycle', False):
            return {'eligible': False, 'reason': '配对非当轮活跃(跨期持仓)', 'code': 'NOT_ELIGIBLE'}
        
        # 条件3: cycle_id匹配（自动满足）
        
        # 条件4: 无未平实例
        if pair_key in self.open_instances:
            return {'eligible': False, 'reason': '已有未平实例', 'code': 'HAS_OPEN_INSTANCE'}
        
        return {'eligible': True, 'reason': 'OK', 'code': 'OK'}
    
    def _create_open_instance(self, pair_key: Tuple[str, str], 
                            cycle_id: int, intent_date: int) -> int:
        """创建开仓实例"""
        # 获取或初始化实例计数器（永不回退）
        if pair_key not in self.instance_counters:
            self.instance_counters[pair_key] = 1
        else:
            self.instance_counters[pair_key] += 1
        
        instance_id = self.instance_counters[pair_key]
        
        # 创建实例
        self.open_instances[pair_key] = {
            'instance_id': instance_id,
            'cycle_id_start': cycle_id,
            'intended_entry_date': intent_date,
            'entry_time': None,
            'last_exec_state': 'pending_entry',
            'leg_qtys': {}
        }
        
        self.algorithm.Debug(f"[CPM] 创建实例: {pair_key} #instance_{instance_id}")
        return instance_id
    
    # === 风控查询接口 ===
    def get_risk_alerts(self) -> Dict:
        """
        供RiskManagement查询的统一接口
        
        Returns:
            Dict: 包含各类风控警报信息
                - expired_pairs: 过期配对列表
                - long_holding_pairs: 长期持仓配对（未来实现）
                - single_leg_instances: 单腿实例（未来实现）
        """
        alerts = {
            'expired_pairs': self.expired_pairs.copy(),  # 返回副本避免外部修改
        }
        
        # 未来可以添加更多风控信息
        # alerts['long_holding_pairs'] = self._get_long_holding_pairs()
        # alerts['single_leg_instances'] = self._get_incomplete_instances()
        
        return alerts
    
    def clear_expired_pairs(self):
        """清空过期配对列表（RiskManagement处理完后调用）"""
        self.expired_pairs = []
    
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
        
        for pair_key, info in self.current_active.items():
            # 只返回本周期活跃且有持仓的配对
            if info.get('eligible_in_cycle', True) and pair_key in self.open_instances:
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
    
    # === 未来Execution接口（占位）===
    def on_execution_filled(self, symbol: str, quantity: float, fill_time) -> None:
        """
        处理成交回报（v2实现）
        
        TODO:
        - 检测两腿都成，更新entry_time
        - 找到对应的prepare_open意图（通过pair_key + instance_id），标记fulfilled=True
        - 检测两腿归零，计算exit_time/holding_days/pnl
        - 找到对应的prepare_close意图（通过pair_key + instance_id），标记fulfilled=True
        - 落history_log
        - 从open_instances删除该pair_key（关键！）
        """
        pass