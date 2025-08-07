# region imports
from AlgorithmImports import *
from typing import List, Tuple, Dict, Set, Optional, TYPE_CHECKING
from enum import Enum
from datetime import datetime, timedelta
from collections import defaultdict

if TYPE_CHECKING:
    from AlgorithmImports import Symbol
# endregion


class PairState(Enum):
    """配对状态枚举"""
    CANDIDATE = "candidate"      # 候选（AlphaModel选中）
    APPROVED = "approved"        # 批准（通过风控）
    ACTIVE = "active"           # 活跃（已建仓）
    CLOSING = "closing"         # 平仓中
    COOLDOWN = "cooldown"       # 冷却期
    BLOCKED = "blocked"         # 被阻止（违反规则）
    AVAILABLE = "available"     # 可用（冷却期结束）


class PairInfo:
    """配对信息"""
    def __init__(self, symbol1, symbol2):
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.pair_id = self._generate_id(symbol1, symbol2)
        
        # State management
        self.state = PairState.CANDIDATE
        self.state_history = []
        
        # Time tracking
        self.first_seen = None
        self.last_entry = None
        self.last_exit = None
        self.cooldown_end = None
        
        # Performance tracking
        self.total_trades = 0
        self.total_pnl = 0
        self.win_rate = 0
        self.avg_holding_days = 0
        
        # Risk metrics
        self.max_drawdown = 0
        self.current_holding_days = 0
    
    def _generate_id(self, symbol1, symbol2) -> str:
        """Generate standardized pair ID"""
        if symbol1.Value < symbol2.Value:
            return f"{symbol1.Value}&{symbol2.Value}"
        return f"{symbol2.Value}&{symbol1.Value}"


class CentralPairManager:
    """
    中央配对管理器 - 策略的核心协调器
    
    Phase 1 MVP版本，实现核心功能：
    1. 前置风控检查（冷却期、单股票限制、配对数限制）
    2. 配对状态管理
    3. 生命周期追踪
    
    设计原则：
    - Single Source of Truth：配对状态的唯一权威
    - Fail-Fast：前置所有检查，尽早发现问题
    - Minimal Disruption：渐进式改造，保持稳定性
    """
    
    def __init__(self, algorithm, config):
        """
        初始化中央配对管理器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 配置字典
        """
        self.algorithm = algorithm
        
        # Configuration parameters
        self.enabled = config.get('enable_central_pair_manager', True)
        self.max_pairs = config.get('max_pairs', 4)
        self.max_symbol_repeats = config.get('max_symbol_repeats', 1)
        self.cooldown_days = config.get('cooldown_days', 7)
        self.max_holding_days = config.get('max_holding_days', 30)
        self.min_quality_score = config.get('min_quality_score', 0.3)
        
        # State storage
        self.pair_states = {}  # {pair_id: PairInfo}
        self.symbol_usage = defaultdict(set)  # {symbol: set(pair_ids)}
        self.historical_pairs = []  # Complete history
        
        # Performance cache
        self.active_pairs_cache = []
        self.cooldown_pairs_cache = []
        self.last_cache_update = None
        
        self.algorithm.Debug(
            f"[CPM] CentralPairManager初始化 - "
            f"最大配对数: {self.max_pairs}, "
            f"单股票限制: {self.max_symbol_repeats}, "
            f"冷却期: {self.cooldown_days}天"
        )
    
    def evaluate_candidates(self, candidates: List[Tuple]) -> List[Tuple]:
        """
        评估候选配对 - AlphaModel调用
        
        这是前置风控的核心方法，在信号生成前执行所有检查。
        
        Args:
            candidates: 候选配对列表 [(symbol1, symbol2), ...]
            
        Returns:
            List[Tuple]: 批准的配对列表
        """
        # If disabled, return all candidates
        if not self.enabled:
            return candidates
            
        approved_pairs = []
        rejection_reasons = defaultdict(list)
        
        # Update cache
        self._update_cache()
        
        # Current active pair count
        active_count = len(self.active_pairs_cache)
        
        for symbol1, symbol2 in candidates:
            pair_id = self._get_pair_id(symbol1, symbol2)
            
            # Get or create pair info
            if pair_id not in self.pair_states:
                self.pair_states[pair_id] = PairInfo(symbol1, symbol2)
                self.pair_states[pair_id].first_seen = self.algorithm.Time
            
            pair_info = self.pair_states[pair_id]
            
            # === Execute all pre-checks ===
            
            # Check 1: Cooldown period
            if self._is_in_cooldown(pair_info):
                days_remaining = (pair_info.cooldown_end - self.algorithm.Time).days if pair_info.cooldown_end else 0
                rejection_reasons[pair_id].append(f"冷却期还剩{days_remaining}天")
                self.algorithm.Debug(
                    f"[CPM] 拒绝 {pair_id}: 冷却期还剩{days_remaining}天"
                )
                continue
                
            # Check 2: Single stock pair limit
            symbol1_violation = self._check_symbol_limit_violation(symbol1, pair_id)
            symbol2_violation = self._check_symbol_limit_violation(symbol2, pair_id)
            
            if symbol1_violation or symbol2_violation:
                if symbol1_violation:
                    active_pairs_s1 = self._get_active_pairs_for_symbol(symbol1)
                    rejection_reasons[pair_id].append(f"{symbol1.Value}已参与{active_pairs_s1}")
                    self.algorithm.Debug(
                        f"[CPM] 拒绝 {pair_id}: {symbol1.Value}已参与其他配对"
                    )
                if symbol2_violation:
                    active_pairs_s2 = self._get_active_pairs_for_symbol(symbol2)
                    rejection_reasons[pair_id].append(f"{symbol2.Value}已参与{active_pairs_s2}")
                    self.algorithm.Debug(
                        f"[CPM] 拒绝 {pair_id}: {symbol2.Value}已参与其他配对"
                    )
                continue
                
            # Check 3: Global pair limit
            if active_count >= self.max_pairs:
                rejection_reasons[pair_id].append(f"已达最大配对数{self.max_pairs}")
                self.algorithm.Debug(
                    f"[CPM] 拒绝 {pair_id}: 已达最大配对数限制"
                )
                break  # No more pairs can be added
                
            # Check 4: Already active (should not re-enter active pairs)
            if pair_info.state == PairState.ACTIVE:
                rejection_reasons[pair_id].append("配对已经活跃")
                self.algorithm.Debug(
                    f"[CPM] 拒绝 {pair_id}: 配对已经活跃"
                )
                continue
            
            # === Passed all checks, approve the pair ===
            approved_pairs.append((symbol1, symbol2))
            self._update_state(pair_info, PairState.APPROVED)
            active_count += 1  # Increment for next iteration
            
            self.algorithm.Debug(
                f"[CPM] 批准配对 {pair_id}: "
                f"第{pair_info.total_trades+1}次交易"
            )
        
        # Summary log
        self.algorithm.Debug(
            f"[CPM] 评估完成: {len(candidates)}个候选, "
            f"{len(approved_pairs)}个批准, "
            f"{len(rejection_reasons)}个拒绝"
        )
        
        return approved_pairs
    
    def register_entry(self, symbol1, symbol2):
        """
        登记建仓 - PortfolioConstruction调用
        
        Args:
            symbol1, symbol2: 配对的两只股票
        """
        if not self.enabled:
            return
            
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id not in self.pair_states:
            # This shouldn't happen if evaluate_candidates was called
            self.algorithm.Debug(f"[CPM] 警告: 未知配对建仓 {pair_id}")
            self.pair_states[pair_id] = PairInfo(symbol1, symbol2)
            self.pair_states[pair_id].first_seen = self.algorithm.Time
            
        pair_info = self.pair_states[pair_id]
        
        # Update state
        self._update_state(pair_info, PairState.ACTIVE)
        pair_info.last_entry = self.algorithm.Time
        pair_info.current_holding_days = 0
        
        # Update symbol usage
        self.symbol_usage[symbol1].add(pair_id)
        self.symbol_usage[symbol2].add(pair_id)
        
        # Update cache
        if pair_info not in self.active_pairs_cache:
            self.active_pairs_cache.append(pair_info)
        
        self.algorithm.Debug(
            f"[CPM] 登记建仓 {pair_id}: "
            f"当前活跃{len(self.active_pairs_cache)}对"
        )
        
    def register_exit(self, symbol1, symbol2, pnl=0):
        """
        登记平仓 - RiskManagement或PC调用
        
        Args:
            symbol1, symbol2: 配对的两只股票
            pnl: 盈亏（可选）
        """
        if not self.enabled:
            return
            
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id not in self.pair_states:
            self.algorithm.Debug(f"[CPM] 警告: 未知配对平仓 {pair_id}")
            return
            
        pair_info = self.pair_states[pair_id]
        
        # Calculate holding days
        holding_days = 0
        if pair_info.last_entry:
            holding_days = (self.algorithm.Time - pair_info.last_entry).days
        
        # Update state
        self._update_state(pair_info, PairState.CLOSING)
        pair_info.last_exit = self.algorithm.Time
        pair_info.cooldown_end = self.algorithm.Time + timedelta(days=self.cooldown_days)
        
        # Update performance stats
        pair_info.total_trades += 1
        pair_info.total_pnl += pnl
        if pair_info.total_trades > 0:
            pair_info.avg_holding_days = (
                (pair_info.avg_holding_days * (pair_info.total_trades - 1) + holding_days) 
                / pair_info.total_trades
            )
            pair_info.win_rate = 1.0 if pair_info.total_pnl > 0 else 0.0
        
        # Clear symbol usage
        self.symbol_usage[symbol1].discard(pair_id)
        self.symbol_usage[symbol2].discard(pair_id)
        
        # Update cache
        if pair_info in self.active_pairs_cache:
            self.active_pairs_cache.remove(pair_info)
        if pair_info not in self.cooldown_pairs_cache:
            self.cooldown_pairs_cache.append(pair_info)
        
        # Transition to COOLDOWN state
        self._update_state(pair_info, PairState.COOLDOWN)
        
        self.algorithm.Debug(
            f"[CPM] 登记平仓 {pair_id}: "
            f"持仓{holding_days}天, "
            f"进入{self.cooldown_days}天冷却期"
        )
    
    def get_active_pairs(self) -> List[Dict]:
        """
        获取活跃配对信息 - RiskManagement调用
        
        Returns:
            List[Dict]: 活跃配对信息列表
        """
        if not self.enabled:
            return []
            
        self._update_cache()
        
        result = []
        for pair_info in self.active_pairs_cache:
            holding_days = 0
            if pair_info.last_entry:
                holding_days = (self.algorithm.Time - pair_info.last_entry).days
                
            result.append({
                'pair': (pair_info.symbol1, pair_info.symbol2),
                'holding_days': holding_days,
                'entry_time': pair_info.last_entry,
                'total_trades': pair_info.total_trades
            })
        
        return result
    
    def is_in_cooldown(self, symbol1, symbol2) -> bool:
        """
        检查配对是否在冷却期
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            bool: 是否在冷却期
        """
        if not self.enabled:
            return False
            
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id in self.pair_states:
            return self._is_in_cooldown(self.pair_states[pair_id])
        return False
    
    def get_statistics(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            Dict: 统计信息字典
        """
        if not self.enabled:
            return {}
            
        self._update_cache()
        
        total_pairs = len(self.pair_states)
        active_pairs = len(self.active_pairs_cache)
        cooldown_pairs = len(self.cooldown_pairs_cache)
        
        # Calculate overall win rate
        winning_pairs = sum(1 for p in self.pair_states.values() if p.total_pnl > 0)
        traded_pairs = sum(1 for p in self.pair_states.values() if p.total_trades > 0)
        overall_win_rate = winning_pairs / traded_pairs if traded_pairs > 0 else 0
        
        return {
            'total_pairs_seen': total_pairs,
            'active_pairs': active_pairs,
            'cooldown_pairs': cooldown_pairs,
            'overall_win_rate': overall_win_rate,
            'total_pnl': sum(p.total_pnl for p in self.pair_states.values()),
            'avg_holding_days': sum(p.avg_holding_days for p in self.pair_states.values()) / total_pairs if total_pairs > 0 else 0
        }
    
    # === Internal helper methods ===
    
    def _get_pair_id(self, symbol1, symbol2) -> str:
        """Generate standardized pair ID"""
        if symbol1.Value < symbol2.Value:
            return f"{symbol1.Value}&{symbol2.Value}"
        return f"{symbol2.Value}&{symbol1.Value}"
    
    def _update_state(self, pair_info: PairInfo, new_state: PairState):
        """Update pair state and record history"""
        old_state = pair_info.state
        pair_info.state = new_state
        pair_info.state_history.append({
            'from': old_state,
            'to': new_state,
            'time': self.algorithm.Time
        })
    
    def _is_in_cooldown(self, pair_info: PairInfo) -> bool:
        """Check if pair is in cooldown period"""
        if pair_info.state == PairState.COOLDOWN:
            if pair_info.cooldown_end and self.algorithm.Time < pair_info.cooldown_end:
                return True
            else:
                # Cooldown period ended, update state
                self._update_state(pair_info, PairState.AVAILABLE)
                if pair_info in self.cooldown_pairs_cache:
                    self.cooldown_pairs_cache.remove(pair_info)
        return False
    
    def _check_symbol_limit_violation(self, symbol, current_pair_id: str) -> bool:
        """
        Check if symbol violates the max_symbol_repeats limit
        
        Args:
            symbol: The symbol to check
            current_pair_id: The pair being evaluated (to exclude from count)
            
        Returns:
            bool: True if limit is violated
        """
        active_pairs = self.symbol_usage.get(symbol, set())
        # Count only other active pairs (exclude current pair being evaluated)
        other_active_pairs = active_pairs - {current_pair_id}
        return len(other_active_pairs) >= self.max_symbol_repeats
    
    def _get_active_pairs_for_symbol(self, symbol) -> str:
        """Get active pairs for a symbol (for logging)"""
        active_pairs = self.symbol_usage.get(symbol, set())
        if active_pairs:
            return ', '.join(active_pairs)
        return ""
    
    def _update_cache(self):
        """Update cache periodically"""
        # Update cache once per day
        if self.last_cache_update and (self.algorithm.Time - self.last_cache_update).days < 1:
            return
            
        # Update active pairs cache
        self.active_pairs_cache = [
            p for p in self.pair_states.values() 
            if p.state == PairState.ACTIVE
        ]
        
        # Update cooldown pairs cache
        self.cooldown_pairs_cache = [
            p for p in self.pair_states.values() 
            if p.state == PairState.COOLDOWN
        ]
        
        # Clean expired cooldown pairs
        for pair_info in self.cooldown_pairs_cache[:]:
            if not self._is_in_cooldown(pair_info):
                if pair_info in self.cooldown_pairs_cache:
                    self.cooldown_pairs_cache.remove(pair_info)
        
        self.last_cache_update = self.algorithm.Time