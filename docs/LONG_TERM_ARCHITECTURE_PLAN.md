# 贝叶斯协整策略长期架构优化方案

## 执行摘要

本文档详细记录了从根本上优化贝叶斯协整策略的架构方案，旨在解决当前系统中的核心问题并建立可持续盈利的长期架构。

创建时间：2025-01-07
版本：v1.0

## 一、问题诊断

### 1.1 核心问题
经过深入分析，当前架构存在以下致命缺陷：

1. **信息孤岛问题**
   - PairRegistry每月完全覆盖历史数据
   - OrderTracker依赖PairRegistry识别配对
   - 导致历史信息丢失，冷却期机制失效

2. **责任不清问题**
   - 没有统一的配对生命周期管理
   - 风控检查分散在多个模块
   - 导致规则执行不一致

3. **时序混乱问题**
   - 风控检查在PortfolioConstruction之后
   - 导致违规信号已经生成才被拦截
   - 资源浪费且容易出错

### 1.2 具体表现
- CMG-AMZN配对违反7天冷却期（2天、4天、1天后重新开仓）
- AMZN同时参与多个配对（违反max_symbol_repeats=1）
- 持仓时间计算错误（累积历史持仓）

## 二、架构设计理念

### 2.1 核心转变
**从"被动响应"转向"主动管理"**
- 当前：各模块独立运作，缺乏协调
- 目标：中央协调器统一管理配对生命周期

### 2.2 设计原则
1. **Single Source of Truth**：配对状态唯一权威源
2. **Fail-Fast**：前置所有检查，尽早发现问题
3. **Audit Trail**：完整的决策记录和历史追踪
4. **Minimal Disruption**：渐进式改造，保持稳定性

## 三、核心架构方案

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     CentralPairManager                       │
│  (新增核心组件 - 配对生命周期的唯一真相源)                    │
├─────────────────────────────────────────────────────────────┤
│ 职责：                                                        │
│ • 维护所有配对的完整生命周期状态                               │
│ • 执行前置风控检查                                            │
│ • 管理历史记录和冷却期                                        │
│ • 协调各模块间的信息流                                        │
└─────────────────────────────────────────────────────────────┘
                              ↑
                              │ 查询/更新
                ┌─────────────┼─────────────┐
                ↓             ↓             ↓
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │AlphaModel│   │Portfolio │   │   Risk   │
        │          │   │Construct │   │Management│
        └──────────┘   └──────────┘   └──────────┘
```

### 3.2 状态流转图

```
[CANDIDATE] ──评估──> [APPROVED] ──建仓──> [ACTIVE]
                          ↓                    ↓
                      [BLOCKED]            [CLOSING]
                                               ↓
                                          [COOLDOWN]
                                               ↓
                                         [AVAILABLE]
```

## 四、详细实施方案

### 4.1 CentralPairManager设计

```python
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
        
        # 状态管理
        self.state = PairState.CANDIDATE
        self.state_history = []
        
        # 时间记录
        self.first_seen = None
        self.last_entry = None
        self.last_exit = None
        self.cooldown_end = None
        
        # 性能追踪
        self.total_trades = 0
        self.total_pnl = 0
        self.win_rate = 0
        self.avg_holding_days = 0
        
        # 风险指标
        self.max_drawdown = 0
        self.current_holding_days = 0

class CentralPairManager:
    """
    中央配对管理器 - 策略的核心协调器
    
    这是整个架构优化的核心组件，负责：
    1. 统一管理所有配对的生命周期
    2. 前置执行所有风控检查
    3. 维护完整的历史记录
    4. 协调各模块的信息流
    """
    
    def __init__(self, algorithm, config):
        self.algorithm = algorithm
        
        # 配置参数
        self.max_pairs = config.get('max_pairs', 4)
        self.max_symbol_repeats = config.get('max_symbol_repeats', 1)
        self.cooldown_days = config.get('cooldown_days', 7)
        self.max_holding_days = config.get('max_holding_days', 30)
        self.min_quality_score = config.get('min_quality_score', 0.3)
        
        # 状态存储
        self.pair_states = {}  # {pair_id: PairInfo}
        self.symbol_usage = defaultdict(set)  # {symbol: set(pair_ids)}
        self.historical_pairs = []  # 完整历史记录
        
        # 性能缓存
        self.active_pairs_cache = []
        self.cooldown_pairs_cache = []
        self.last_cache_update = None
        
    def evaluate_candidates(self, candidates: List[Tuple]) -> List[Tuple]:
        """
        评估候选配对 - AlphaModel调用
        
        这是前置风控的核心方法，在信号生成前执行所有检查
        """
        approved_pairs = []
        rejection_reasons = defaultdict(list)
        
        # 更新缓存
        self._update_cache()
        
        for symbol1, symbol2 in candidates:
            pair_id = self._get_pair_id(symbol1, symbol2)
            
            # 获取或创建配对信息
            if pair_id not in self.pair_states:
                self.pair_states[pair_id] = PairInfo(symbol1, symbol2)
                self.pair_states[pair_id].first_seen = self.algorithm.Time
            
            pair_info = self.pair_states[pair_id]
            
            # === 执行所有前置检查 ===
            
            # 检查1：冷却期
            if self._is_in_cooldown(pair_info):
                days_remaining = (pair_info.cooldown_end - self.algorithm.Time).days
                rejection_reasons[pair_id].append(f"冷却期还剩{days_remaining}天")
                continue
                
            # 检查2：单股票配对限制
            if self._violates_symbol_limit(symbol1, symbol2):
                active_pairs_s1 = self._get_active_pairs_for_symbol(symbol1)
                active_pairs_s2 = self._get_active_pairs_for_symbol(symbol2)
                if active_pairs_s1:
                    rejection_reasons[pair_id].append(f"{symbol1.Value}已参与{active_pairs_s1}")
                if active_pairs_s2:
                    rejection_reasons[pair_id].append(f"{symbol2.Value}已参与{active_pairs_s2}")
                continue
                
            # 检查3：全局配对数限制
            active_count = len(self.active_pairs_cache)
            if active_count >= self.max_pairs:
                rejection_reasons[pair_id].append(f"已达最大配对数{self.max_pairs}")
                break  # 不再评估后续配对
                
            # 检查4：历史表现（可选）
            if self._has_poor_history(pair_info):
                rejection_reasons[pair_id].append(
                    f"历史表现不佳：胜率{pair_info.win_rate:.1%}，"
                    f"最大回撤{pair_info.max_drawdown:.1%}"
                )
                continue
                
            # 检查5：是否已经是活跃配对
            if pair_info.state == PairState.ACTIVE:
                rejection_reasons[pair_id].append("配对已经活跃")
                continue
            
            # === 通过所有检查，批准配对 ===
            approved_pairs.append((symbol1, symbol2))
            self._update_state(pair_info, PairState.APPROVED)
            
            self.algorithm.Debug(
                f"[CPM] 批准配对 {pair_id}: "
                f"第{pair_info.total_trades+1}次交易"
            )
        
        # 输出拒绝原因汇总
        if rejection_reasons:
            self.algorithm.Debug("[CPM] 配对拒绝原因汇总：")
            for pair_id, reasons in rejection_reasons.items():
                self.algorithm.Debug(f"  {pair_id}: {'; '.join(reasons)}")
        
        self.algorithm.Debug(
            f"[CPM] 评估完成：{len(candidates)}个候选，"
            f"{len(approved_pairs)}个批准，"
            f"{len(rejection_reasons)}个拒绝"
        )
        
        return approved_pairs
    
    def register_entry(self, symbol1, symbol2):
        """登记建仓 - PortfolioConstruction调用"""
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id not in self.pair_states:
            self.algorithm.Debug(f"[CPM] 警告：未知配对建仓 {pair_id}")
            return
            
        pair_info = self.pair_states[pair_id]
        
        # 更新状态
        self._update_state(pair_info, PairState.ACTIVE)
        pair_info.last_entry = self.algorithm.Time
        pair_info.current_holding_days = 0
        
        # 更新股票使用记录
        self.symbol_usage[symbol1].add(pair_id)
        self.symbol_usage[symbol2].add(pair_id)
        
        # 更新缓存
        if pair_info not in self.active_pairs_cache:
            self.active_pairs_cache.append(pair_info)
        
        self.algorithm.Debug(
            f"[CPM] 登记建仓 {pair_id}: "
            f"当前活跃{len(self.active_pairs_cache)}对"
        )
        
    def register_exit(self, symbol1, symbol2, pnl=0):
        """登记平仓 - RiskManagement或PC调用"""
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id not in self.pair_states:
            self.algorithm.Debug(f"[CPM] 警告：未知配对平仓 {pair_id}")
            return
            
        pair_info = self.pair_states[pair_id]
        
        # 更新状态
        self._update_state(pair_info, PairState.CLOSING)
        pair_info.last_exit = self.algorithm.Time
        pair_info.cooldown_end = self.algorithm.Time + timedelta(days=self.cooldown_days)
        
        # 更新性能统计
        pair_info.total_trades += 1
        pair_info.total_pnl += pnl
        holding_days = (self.algorithm.Time - pair_info.last_entry).days if pair_info.last_entry else 0
        pair_info.avg_holding_days = (
            (pair_info.avg_holding_days * (pair_info.total_trades - 1) + holding_days) 
            / pair_info.total_trades
        )
        
        # 清理股票使用记录
        self.symbol_usage[symbol1].discard(pair_id)
        self.symbol_usage[symbol2].discard(pair_id)
        
        # 更新缓存
        if pair_info in self.active_pairs_cache:
            self.active_pairs_cache.remove(pair_info)
        self.cooldown_pairs_cache.append(pair_info)
        
        # 延迟状态转换到COOLDOWN
        self._update_state(pair_info, PairState.COOLDOWN)
        
        self.algorithm.Debug(
            f"[CPM] 登记平仓 {pair_id}: "
            f"持仓{holding_days}天，PnL={pnl:.2f}，"
            f"进入{self.cooldown_days}天冷却期"
        )
    
    def get_active_pairs(self) -> List[Dict]:
        """获取活跃配对信息 - RiskManagement调用"""
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
        """检查是否在冷却期"""
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id in self.pair_states:
            return self._is_in_cooldown(self.pair_states[pair_id])
        return False
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        self._update_cache()
        
        total_pairs = len(self.pair_states)
        active_pairs = len(self.active_pairs_cache)
        cooldown_pairs = len(self.cooldown_pairs_cache)
        
        # 计算整体胜率
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
    
    # === 内部辅助方法 ===
    
    def _get_pair_id(self, symbol1, symbol2) -> str:
        """生成标准化的配对ID"""
        if symbol1.Value < symbol2.Value:
            return f"{symbol1.Value}&{symbol2.Value}"
        return f"{symbol2.Value}&{symbol1.Value}"
    
    def _update_state(self, pair_info: PairInfo, new_state: PairState):
        """更新配对状态并记录历史"""
        old_state = pair_info.state
        pair_info.state = new_state
        pair_info.state_history.append({
            'from': old_state,
            'to': new_state,
            'time': self.algorithm.Time
        })
    
    def _is_in_cooldown(self, pair_info: PairInfo) -> bool:
        """检查配对是否在冷却期"""
        if pair_info.state == PairState.COOLDOWN:
            if pair_info.cooldown_end and self.algorithm.Time < pair_info.cooldown_end:
                return True
            else:
                # 冷却期结束，更新状态
                self._update_state(pair_info, PairState.AVAILABLE)
                if pair_info in self.cooldown_pairs_cache:
                    self.cooldown_pairs_cache.remove(pair_info)
        return False
    
    def _violates_symbol_limit(self, symbol1, symbol2) -> bool:
        """检查是否违反单股票配对限制"""
        # 检查symbol1
        active_pairs_s1 = self.symbol_usage.get(symbol1, set())
        if len(active_pairs_s1) >= self.max_symbol_repeats:
            return True
            
        # 检查symbol2
        active_pairs_s2 = self.symbol_usage.get(symbol2, set())
        if len(active_pairs_s2) >= self.max_symbol_repeats:
            return True
            
        return False
    
    def _get_active_pairs_for_symbol(self, symbol) -> str:
        """获取股票参与的活跃配对"""
        active_pairs = self.symbol_usage.get(symbol, set())
        if active_pairs:
            return ', '.join(active_pairs)
        return ""
    
    def _has_poor_history(self, pair_info: PairInfo) -> bool:
        """检查历史表现是否不佳"""
        if pair_info.total_trades < 2:
            return False  # 交易次数太少，不判断
            
        # 检查胜率
        if pair_info.win_rate < 0.3:  # 胜率低于30%
            return True
            
        # 检查最大回撤
        if pair_info.max_drawdown < -0.2:  # 最大回撤超过20%
            return True
            
        return False
    
    def _update_cache(self):
        """更新缓存"""
        # 每天更新一次缓存
        if self.last_cache_update and (self.algorithm.Time - self.last_cache_update).days < 1:
            return
            
        # 更新活跃配对缓存
        self.active_pairs_cache = [
            p for p in self.pair_states.values() 
            if p.state == PairState.ACTIVE
        ]
        
        # 更新冷却期配对缓存
        self.cooldown_pairs_cache = [
            p for p in self.pair_states.values() 
            if p.state == PairState.COOLDOWN
        ]
        
        # 清理过期的冷却期配对
        for pair_info in self.cooldown_pairs_cache[:]:
            if not self._is_in_cooldown(pair_info):
                self.cooldown_pairs_cache.remove(pair_info)
        
        self.last_cache_update = self.algorithm.Time
```

## 五、模块改造方案

### 5.1 AlphaModel改造

```python
# AlphaModel.py 关键改动
def update(self, algorithm, data):
    """集成CentralPairManager的前置风控"""
    
    # 原有的配对选择逻辑
    candidates = self._select_pairs(symbols)
    
    # 新增：通过CentralPairManager评估
    if hasattr(algorithm, 'central_pair_manager'):
        approved_pairs = algorithm.central_pair_manager.evaluate_candidates(candidates)
    else:
        # 兼容旧版本
        approved_pairs = candidates
    
    # 只对批准的配对生成信号
    insights = []
    for symbol1, symbol2 in approved_pairs:
        # 生成信号
        insight = self._generate_insight(symbol1, symbol2)
        if insight:
            insights.append(insight)
    
    return insights
```

### 5.2 PortfolioConstruction改造

```python
# PortfolioConstruction.py 关键改动
def create_targets(self, algorithm, insights):
    """集成CentralPairManager的建仓登记"""
    
    targets = []
    
    for group_id, group in grouped_insights.items():
        # 原有逻辑
        symbol1, symbol2 = self._parse_pair(group)
        
        # 生成目标
        if direction != InsightDirection.Flat:
            # 新增：登记建仓
            if hasattr(algorithm, 'central_pair_manager'):
                algorithm.central_pair_manager.register_entry(symbol1, symbol2)
        
        targets.extend(self._create_targets(symbol1, symbol2, direction))
    
    return targets
```

### 5.3 RiskManagement简化

```python
# RiskManagement.py 简化版
class BayesianCointegrationRiskManagementModel:
    """简化的风险管理 - 专注实时风控"""
    
    def ManageRisk(self, algorithm, targets):
        """只负责T+0实时检查"""
        
        # 从CentralPairManager获取活跃配对
        if hasattr(algorithm, 'central_pair_manager'):
            active_pairs = algorithm.central_pair_manager.get_active_pairs()
        else:
            # 兼容旧版本
            active_pairs = self._get_active_pairs()
        
        liquidations = []
        
        for pair_info in active_pairs:
            symbol1, symbol2 = pair_info['pair']
            
            # T+0检查（自下而上）
            # Level 1: 个股止损
            if self._check_single_drawdown(symbol1, symbol2):
                liquidations.append((symbol1, symbol2, "个股止损"))
                
            # Level 2: 配对止损
            elif self._check_pair_drawdown(symbol1, symbol2):
                liquidations.append((symbol1, symbol2, "配对止损"))
                
            # Level 3: 持仓超时（从CPM获取）
            elif pair_info['holding_days'] > self.max_holding_days:
                liquidations.append((symbol1, symbol2, "持仓超时"))
        
        # 生成平仓指令
        for symbol1, symbol2, reason in liquidations:
            # 登记平仓
            if hasattr(algorithm, 'central_pair_manager'):
                algorithm.central_pair_manager.register_exit(symbol1, symbol2)
            
            # 生成targets
            targets.extend(self._create_liquidation_targets(symbol1, symbol2))
        
        return targets
```

## 六、实施路线图

### Phase 1：最小可行产品（1-2天）
**目标**：立即解决冷却期和单股票配对限制问题

**任务清单**：
1. [ ] 创建 `src/CentralPairManager.py`
2. [ ] 在 `main.py` 中初始化 CentralPairManager
3. [ ] 修改 AlphaModel 集成前置检查
4. [ ] 修改 PortfolioConstruction 登记建仓
5. [ ] 修改 RiskManagement 登记平仓
6. [ ] 添加配置开关以便回滚
7. [ ] 运行回测验证

**验收标准**：
- 冷却期机制100%生效
- 单股票不再同时参与多个配对
- 回测结果稳定

### Phase 2：完善状态管理（3-5天）
**目标**：增强配对生命周期管理

**任务清单**：
1. [ ] 实现完整的状态转换逻辑
2. [ ] 添加历史表现追踪
3. [ ] 实现智能配对评分系统
4. [ ] 添加性能统计和报告
5. [ ] 优化缓存机制

**验收标准**：
- 配对质量提升20%以上
- 亏损配对自动过滤
- 详细的性能报告

### Phase 3：性能优化（1周）
**目标**：提升系统性能和稳定性

**任务清单**：
1. [ ] 优化MCMC采样（实现结果缓存）
2. [ ] 实现增量式协整检测
3. [ ] 添加并行处理能力
4. [ ] 优化内存使用
5. [ ] 添加性能监控

**验收标准**：
- 回测速度提升50%
- 内存使用降低30%
- 系统稳定性提升

### Phase 4：高级功能（2周）
**目标**：实现智能化交易

**任务清单**：
1. [ ] 实现自适应参数调整
2. [ ] 添加机器学习配对预测
3. [ ] 实现多时间框架分析
4. [ ] 添加市场状态识别
5. [ ] 实现动态风控阈值

**验收标准**：
- 夏普比率提升30%
- 最大回撤降低20%
- 适应不同市场环境

## 七、风险和缓解措施

### 7.1 技术风险
- **风险**：新架构可能引入bug
- **缓解**：使用配置开关，保留回滚能力

### 7.2 性能风险
- **风险**：中央管理器可能成为瓶颈
- **缓解**：实现缓存机制，优化查询

### 7.3 兼容性风险
- **风险**：与现有代码不兼容
- **缓解**：渐进式改造，保持接口稳定

## 八、成功指标

### 短期（1个月）
- [ ] 冷却期违规率降至0%
- [ ] 单股票重复配对率降至0%
- [ ] 系统稳定性提升50%

### 中期（3个月）
- [ ] 夏普比率提升20%
- [ ] 最大回撤降低15%
- [ ] 胜率提升10%

### 长期（6个月）
- [ ] 年化收益率稳定在15%以上
- [ ] 夏普比率稳定在1.5以上
- [ ] 最大回撤控制在10%以内

## 九、总结

这个长期架构优化方案通过引入CentralPairManager作为核心协调器，从根本上解决了当前系统的信息孤岛、责任不清和时序混乱问题。方案采用渐进式实施，确保系统稳定性的同时逐步提升性能和功能。

关键成功因素：
1. **前置风控**：在信号生成前完成所有检查
2. **统一管理**：单一数据源管理配对生命周期
3. **渐进实施**：分阶段改造，降低风险
4. **性能优化**：通过缓存和并行处理提升效率
5. **智能化**：逐步引入机器学习和自适应机制

预期收益：
- 立即解决当前的致命问题
- 显著提升策略稳定性和盈利能力
- 为长期发展奠定坚实基础
- 便于未来功能扩展和优化

## 附录A：配置参数说明

```python
# CentralPairManager配置
central_pair_config = {
    'enabled': True,                    # 是否启用（用于回滚）
    'max_pairs': 4,                     # 最大同时持有配对数
    'max_symbol_repeats': 1,            # 单股票最多参与配对数
    'cooldown_days': 7,                 # 冷却期天数
    'max_holding_days': 30,             # 最大持仓天数
    'min_quality_score': 0.3,           # 最低质量分数
    'enable_history_check': False,      # 是否启用历史表现检查（Phase 2）
    'enable_cache': True,               # 是否启用缓存
    'cache_ttl_minutes': 60,           # 缓存有效期（分钟）
}
```

## 附录B：测试计划

### 单元测试
1. CentralPairManager所有方法
2. 状态转换逻辑
3. 冷却期计算
4. 股票使用限制

### 集成测试
1. AlphaModel + CentralPairManager
2. PortfolioConstruction + CentralPairManager
3. RiskManagement + CentralPairManager
4. 完整交易流程

### 回测验证
1. 对比优化前后的关键指标
2. 验证冷却期生效
3. 验证股票配对限制
4. 压力测试（大量配对）

## 文档版本历史

- v1.0 (2025-01-07): 初始版本，详细记录架构优化方案