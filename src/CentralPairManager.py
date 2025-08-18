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
        
        # === 意图管理 ===
        self.intents_log = []          # 意图日志
        self.daily_intent_cache = {}   # 日去重缓存
        self.cache_date = None         # 缓存日期
        
        # === 状态记录 ===
        self.last_cycle_id = None
        self.last_cycle_pairs = set()
        self.history_log = []          # 历史日志（预留）
    
    # === Alpha交互 ===
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
                    # 检查current_pairs和legacy_pairs
                    existing = self.current_pairs.get(pair_key) or self.legacy_pairs.get(pair_key)
                    if existing and (existing['beta'] != pair['beta'] or 
                                    existing['quality_score'] != pair['quality_score']):
                        self.algorithm.Error(f"[CPM] 拒绝修改已冻结的cycle {cycle_id} 参数")
                        return
                
                # 完全相同，幂等返回
                self.algorithm.Debug(f"[CPM] Cycle {cycle_id} 幂等重复提交，忽略")
                return
        
        # 3. 处理周期转换时的配对迁移（仅在新cycle时）
        if self.last_cycle_id is not None and cycle_id != self.last_cycle_id:
            # 3.1 迁移旧的current_pairs
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
            
            # 3.2 清理已平仓的legacy_pairs
            for pair_key in list(self.legacy_pairs.keys()):
                if not self._has_open_instance(pair_key):
                    self._retire_pair(pair_key)
                    del self.legacy_pairs[pair_key]
                    self.algorithm.Debug(f"[CPM] legacy配对已平仓，移至retired: {pair_key}")
        
        # 4. 更新current_pairs（仅包含本轮活跃配对）
        self.current_pairs.clear()  # 清空后重建
        for pair in pairs:
            pair_key = self._make_pair_key(pair['symbol1_value'], pair['symbol2_value'])
            self.current_pairs[pair_key] = {
                'cycle_id': cycle_id,
                'beta': pair['beta'],
                'quality_score': pair['quality_score']
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
    
    def _retire_pair(self, pair_key: Tuple[str, str]) -> None:
        """将配对移至retired_pairs"""
        if pair_key in self.closed_instances:
            exit_time = self.closed_instances[pair_key].get('exit_time')
            self.retired_pairs[pair_key] = {
                'retired_time': exit_time or self.algorithm.Time,
                'cycle_id': self.closed_instances[pair_key].get('cycle_id')
            }
    
    def get_all_tracked_pairs(self) -> Dict:
        """获取所有被跟踪的配对（包括当前轮和遗留）
        
        Returns:
            Dict: 包含current_pairs和legacy_pairs的合并字典
                  每个配对包含is_current标记以区分来源
        """
        # 合并current_pairs和legacy_pairs
        merged = {}
        for k, v in self.current_pairs.items():
            merged[k] = {**v, 'is_current': True}
        for k, v in self.legacy_pairs.items():
            merged[k] = {**v, 'is_current': False}
        return merged
    
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
            # 开仓：从current_pairs或legacy_pairs获取cycle_id
            if pair_key in self.current_pairs:
                cycle_id = self.current_pairs[pair_key].get('cycle_id')
            elif pair_key in self.legacy_pairs:
                cycle_id = self.legacy_pairs[pair_key].get('cycle_id')
            else:
                self.algorithm.Error(f"[CPM] 拒绝(NOT_ACTIVE): {pair_key} 不在活跃或遗留列表")
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
        # 条件1: pair_key在current_pairs或legacy_pairs中（已在上层检查）
        if pair_key in self.current_pairs:
            pair_info = self.current_pairs[pair_key]
            eligible_in_cycle = True
        elif pair_key in self.legacy_pairs:
            pair_info = self.legacy_pairs[pair_key]
            eligible_in_cycle = False
        else:
            return {'eligible': False, 'reason': '配对不在活跃或遗留列表', 'code': 'NOT_ACTIVE'}
        
        # 条件2: eligible_in_cycle == True（只有current_pairs中的才能新开仓）
        if not eligible_in_cycle:
            return {'eligible': False, 'reason': '配对非当轮活跃(遗留持仓)', 'code': 'NOT_ELIGIBLE'}
        
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
    
    # === OOE接口（v1.4实现）===
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
    
    # === Alpha查询接口（v1.5实现）===
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