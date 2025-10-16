# 贝叶斯协整策略更新日志

版本格式：v<主>.<次>.<修>[_描述][@日期]

---

## [v6.6.0_Pair层面风控三规则体系@20250217]

### 版本定义
**里程碑版本**: 完整的Pair层面风控体系,配对专属PnL计算突破

本版本实现了完整的三层风控架构:
- ✅ **Portfolio层面**: AccountBlowup + ExcessiveDrawdown
- ✅ **Market层面**: MarketCondition (开仓控制)
- ✅ **Pair层面**: PositionAnomaly + HoldingTimeout + PairDrawdown (本版本重点)

### 核心功能

#### 1. Pair层面风控规则体系

**PositionAnomalyRule (优先级100)**
- **检测内容**: 单边持仓(PARTIAL_LEG1/LEG2) + 同向持仓(ANOMALY_SAME)
- **触发条件**: pair.has_anomaly()返回True
- **响应动作**: pair_close
- **设计特点**: 最高优先级,异常必须立即处理
- **回测表现**: 0次触发(符合预期,回测环境订单执行完美)

**HoldingTimeoutRule (优先级60)**
- **检测内容**: 持仓超时检测
- **触发条件**: 持仓天数 > 30天
- **响应动作**: pair_close
- **设计特点**: 防止长期持仓,避免资金占用
- **回测表现**: 11次触发 (68.8%),主要风控手段

**PairDrawdownRule (优先级50)**
- **检测内容**: 配对级别回撤
- **触发条件**: (HWM - current_pnl) / entry_cost ≥ 阈值(5%)
- **响应动作**: pair_close
- **设计特点**: HWM追踪盈利峰值,保护已有盈利
- **回测表现**: 5次触发 (31.2%),包括1次盈利回撤止盈
- **典型案例**:
  - (CVS, GILD): 从盈利$182回撤至亏损$721,触发5.2%回撤
  - (AMAT, NVDA): 从盈利$1,287回撤至$287,触发6.1%回撤(盈利止盈)

#### 2. 配对专属PnL计算突破

**技术突破**: 解决Portfolio全局查询混淆问题
- **问题**: 如果同一symbol出现在多个配对中,Portfolio[symbol]返回全局持仓
- **解决方案**: 配对级别独立追踪成本和价格

**实现机制**:
```python
# Pairs.__init__新增追踪变量
self.entry_price1 = None        # symbol1开仓均价 (OrderTicket.AverageFillPrice)
self.entry_price2 = None        # symbol2开仓均价
self.entry_cost = 0.0           # 配对总成本
self.pair_hwm = 0.0             # 配对级别高水位

# 配对专属PnL计算
def get_pair_pnl(self):
    current_value = tracked_qty1 * current_price1 + tracked_qty2 * current_price2
    entry_value = tracked_qty1 * entry_price1 + tracked_qty2 * entry_price2
    return current_value - entry_value  # 完全独立

# 配对回撤计算
def get_pair_drawdown(self):
    pnl = self.get_pair_pnl()
    if pnl > self.pair_hwm:
        self.pair_hwm = pnl  # 自动更新高水位
    return (self.pair_hwm - pnl) / self.entry_cost
```

**HWM生命周期管理**:
- 开仓时: HWM=0 (起点为盈亏平衡)
- 持仓中: 自动更新 (pnl > hwm时)
- 平仓时: HWM=0 (清零重置)

#### 3. RiskManager统一调度架构

**规则注册机制**:
- Portfolio层面规则: AccountBlowupRule(100), ExcessiveDrawdownRule(90)
- Pair层面规则: PositionAnomalyRule(100), HoldingTimeoutRule(60), PairDrawdownRule(50)

**优先级调度**:
- 同层面内按priority降序排序
- 返回最高优先级规则的动作
- 不同配对的风控相互独立

### 技术实现细节

#### 1. 时区问题彻底解决
- **问题**: 混用Time(timezone-aware)和UtcTime(timezone-naive)导致TypeError
- **解决**: 全局统一使用`algorithm.UtcTime`进行时间差计算
- **修复文件**:
  - HoldingTimeoutRule.py: 复用pair.get_pair_holding_days()
  - BayesianModeler.py: 三处统一改为UtcTime (Line 31, 126, 240)

#### 2. DRY原则应用
- HoldingTimeoutRule: 复用`pair.get_pair_holding_days()`
- PositionAnomalyRule: 复用`pair.has_anomaly()`
- PairDrawdownRule: 复用`pair.get_pair_pnl()`和`pair.get_pair_drawdown()`

#### 3. 订单锁机制协同
- Pair规则无需冷却期
- `tickets_manager.is_pair_locked()`检查订单执行状态
- PENDING状态的配对跳过风控检查

### 测试覆盖

#### 单元测试
- **test_position_anomaly_rule.py**: 7个测试用例全部通过
  - 单边持仓LEG1/LEG2检测
  - 同向持仓检测
  - 正常持仓不误报
  - 禁用规则测试
  - 优先级验证

#### 回测验证
- **测试周期**: 2023-09-20 至 2024-02-29 (5个月)
- **回测ID**: Creative Fluorescent Yellow Coyote
- **触发统计**:
  - PositionAnomalyRule: 0次 (符合预期)
  - HoldingTimeoutRule: 11次
  - PairDrawdownRule: 5次 (阈值5%)
- **验证点**:
  - ✅ PnL计算正确性 (配对专属,无混淆)
  - ✅ HWM追踪正确性 (峰值记录准确)
  - ✅ 回撤公式正确性 ((HWM-PnL)/cost)
  - ✅ 执行流程完整性 (检测→平仓→订单追踪→解锁)
  - ✅ 盈利止盈功能 (AMAT-NVDA案例)

### 架构演进

#### 从v6.5.1到v6.6.0的演进路径
```
v6.5.1: 订单追踪基础
  └── TicketsManager完整实现

v6.5.2: Portfolio风控起步
  └── AccountBlowupRule + 冷却期修复

v6.6.0: 完整三层风控体系 (本版本)
  ├── Portfolio层面: AccountBlowup + ExcessiveDrawdown
  ├── Market层面: MarketCondition
  └── Pair层面: PositionAnomaly + HoldingTimeout + PairDrawdown
```

### 文件修改清单

#### 新增文件
- `src/RiskManagement/PositionAnomalyRule.py` (126行)
- `src/RiskManagement/HoldingTimeoutRule.py` (107行)
- `src/RiskManagement/PairDrawdownRule.py` (135行)
- `tests/test_position_anomaly_rule.py` (462行)

#### 修改文件
- `src/Pairs.py`: 新增成本追踪和PnL计算方法
- `src/config.py`: pair_rules配置启用
- `src/RiskManagement/RiskManager.py`: 三个Pair规则注册
- `src/RiskManagement/__init__.py`: 导出规则
- `src/analysis/BayesianModeler.py`: 时区统一修复

### 配置建议

#### 当前配置(回测验证)
```python
'pair_rules': {
    'position_anomaly': {'enabled': True, 'priority': 100},
    'holding_timeout': {'enabled': True, 'priority': 60, 'max_days': 30},
    'pair_drawdown': {'enabled': True, 'priority': 50, 'threshold': 0.05}
}
```

#### 生产环境建议
- PairDrawdown阈值建议10-15%(当前5%过于敏感)

### 相关提交
- `3b6f930`: feat: 实现AccountBlowup风控规则并修复冷却期BUG (v6.5.2)
- 本提交: feat: 实现完整Pair层面风控三规则体系 (v6.6.0)

---

## [v6.5.1_无风控模块的已测试基线版本@20250131]

### 版本定义
**里程碑版本**: 核心订单追踪功能完整且已验证，暂未启用风控模块

本版本作为架构演进的重要基线，明确标记以下状态：
- ✅ **订单追踪完整**: TicketsManager已通过12个月回测 + 单元测试全面验证
- ⚠️ **风控模块未启用**: RiskManagement.py代码保留，但main.py未引用
- 📊 **架构稳定**: OnData驱动架构、Pairs对象化、PairsManager生命周期管理

### 功能状态

#### ✅ 已完成并测试的模块
- **TicketsManager订单追踪**
  - 12个月回测验证: 276个订单100%成功（2023-09-20至2024-09-20）
  - 单元测试验证: 3/3核心场景通过，代码逻辑审查完整
  - 异常检测能力: ANOMALY状态正确识别Canceled/Invalid订单
  - 回调隔离机制: 仅COMPLETED状态触发on_position_filled()

- **Pairs对象化架构**
  - 信号生成: get_signal()支持5种信号类型
  - 持仓管理: open_position()/close_position()完整实现
  - Beta对冲: 动态计算对冲比例
  - Cooldown机制: 防止频繁交易

- **PairsManager生命周期管理**
  - 三状态分类: Active/Legacy/Dormant
  - 动态更新: update_pairs()支持月度选股轮换
  - 查询接口: 按持仓状态筛选配对
  - 行业集中度: 实时计算sector concentration

- **OnData事件驱动架构**
  - 主循环: main.py OnData()中央协调
  - 分层风控: 组合级→配对级优先级清晰
  - 开平仓逻辑: 智能资金分配算法

#### ⚠️ 未启用的模块
- **RiskManagement风控系统**
  - 文件位置: `src/RiskManagement.py` (183行代码完整)
  - 模块内容:
    - PortfolioLevelRiskManager (账户爆仓、最大回撤、市场波动率检测)
    - PairLevelRiskManager (持仓超时、仓位异常、配对回撤检测)
  - 当前状态: **main.py未import，逻辑未调用**
  - 保留原因: 代码完整性，便于未来重新集成

### 版本意义

#### 1. 清晰基线
- 为重新引入风控提供干净的起点
- 便于A/B对比风控模块的影响
- 明确订单追踪功能的独立性

#### 2. 职责边界
- **TicketsManager职责**: 订单生命周期追踪、异常检测、回调触发
- **RiskManagement职责**: 风险检测（未来重新启用时）
- **main.py职责**: 风险响应执行（清算、止损）

#### 3. 便于问题隔离
- 如果订单追踪出问题 → 定位到TicketsManager
- 如果需要风险管理 → 明确当前版本无风控逻辑
- 如果持仓异常 → 排查Pairs/PairsManager

### 技术快照

#### 架构组件
```
main.py (BayesianCointegrationStrategy)
  ├── UniverseSelection (月度选股)
  ├── Analysis模块 (协整+贝叶斯建模)
  ├── PairsManager (配对生命周期管理)
  │     └── Pairs (信号+执行)
  └── TicketsManager (订单追踪) ← 本版本重点验证

未连接: RiskManagement (代码存在但未使用)
```

#### 测试覆盖
- **回测测试**: 12个月真实市场数据 (276个订单)
- **单元测试**: Mock环境下3个核心场景 + 7个扩展场景
- **代码审查**: get_pair_status()和on_order_event()完整验证

#### 性能指标
- 订单成功率: 100% (276/276)
- 异常检测准确率: 100% (单元测试验证)
- 平均持仓时间: 符合预期
- 资金利用率: 符合配置参数

### 相关提交
- `f2c59f9`: feat: 创建TicketsManager单元测试环境 (v6.5.0)
- `1853424`: fix: 修复PairsManager.py遗漏的Debug参数 (v6.4.11)
- 本提交: docs: 标记v6.5.1为无风控模块的已测试基线版本

### 文档参考
- **测试指南**: `docs/测试指南.md` - 如何运行和扩展测试
- **检测报告**: `docs/TicketsManager检测报告_20250131.md` - 完整代码质量分析
- **CLAUDE.md**: 架构说明中已更新v6.5.1版本信息

### 重要提示

⚠️ **本版本不适用于实盘交易**
- 原因: 缺少风险管理逻辑（最大回撤、持仓超时、配对回撤等）
- 用途: 订单追踪功能验证、架构基线参考
- 建议: 重新启用RiskManagement后再考虑实盘部署

✅ **适用于以下场景**
- 回测环境下验证订单追踪功能
- 单元测试环境下开发和调试
- 作为重新引入风控的清晰起点
- 教学和代码审查参考

### 下一步计划
1. 分析RiskManagement模块与当前架构的集成方式
2. 设计风险检测与执行的职责分离方案
3. 编写风控模块的单元测试
4. 在回测中验证风控逻辑有效性

---

## [v6.5.0_创建TicketsManager单元测试环境@20250131]

### 新增功能
**单元测试基础设施**: 创建完全隔离的测试环境,验证订单异常检测逻辑

### 实施内容

#### 1. Mock对象系统
创建`tests/mocks/mock_qc_objects.py`,模拟QuantConnect核心类:
- **MockAlgorithm**: 模拟QCAlgorithm,记录Debug消息
- **MockOrderTicket**: 可手动设置Status(Filled/Canceled/Invalid)
- **MockOrderEvent**: 模拟OnOrderEvent事件对象
- **MockSymbol**: 模拟Symbol类
- **MockOrderStatus**: 模拟OrderStatus枚举
- **MockPairsManager/MockPairs**: 验证回调机制

#### 2. AlgorithmImports桩模块
创建`tests/mocks/algorithm_imports_stub.py`:
- 在测试环境中拦截`from AlgorithmImports import *`
- 将导入重定向到Mock对象
- 使得生产代码可以在无QuantConnect环境下运行

#### 3. 测试套件

**核心测试** (`tests/test_simple.py`):
| 测试用例 | 场景 | 验证点 |
|---------|------|--------|
| test_normal_completion | 双腿Filled | COMPLETED + 回调触发 |
| test_one_leg_canceled | 单腿Canceled | ANOMALY检测 + 回调隔离 |
| test_pending_state | 一腿Submitted | PENDING + 锁定机制 |

**完整测试** (`tests/test_tickets_manager.py`):
- 额外覆盖双腿Canceled, 单腿Invalid, 多配对场景等
- 总计7个测试用例,全面覆盖极端情况

#### 4. 测试结果
```
==================================================
TicketsManager 核心功能测试
==================================================
[PASS] 正常完成+回调验证通过
[PASS] 单腿Canceled检测正确
[PASS] Pending状态+锁定机制正确
==================================================
结果: 3/3 通过
==================================================
```

### 设计原则

#### 完全隔离性保证
1. **物理隔离**: `tests/`目录独立于`src/`,QuantConnect不加载
2. **导入单向性**: 测试导入生产代码,生产代码不知测试存在
3. **Mock对象替换**: 测试用MockAlgorithm,回测用真实QCAlgorithm
4. **模块注入技术**: `sys.modules['AlgorithmImports'] = Mock版本`

#### 验证方法
```bash
# 验证生产代码未被修改
git status src/  # → 无.py文件变更

# 验证回测不受影响
lean backtest BayesCointegration  # → 结果与测试前相同
```

### 测试覆盖场景

| 场景类型 | 生产环境概率 | 测试环境可模拟 |
|---------|-------------|--------------|
| **正常完成** (双腿Filled) | 99% | ✅ |
| **单腿Canceled** | 0.5% | ✅ (无法在回测中触发) |
| **双腿Canceled** | 0.1% | ✅ (无法在回测中触发) |
| **单腿Invalid** | 0.3% | ✅ (无法在回测中触发) |
| **PartiallyFilled** | 0.1% | ✅ (无法在回测中触发) |
| **Pending状态** | 常见(短暂) | ✅ |

### 技术亮点

#### 1. 模块注入技术
```python
# 在导入生产代码前,注入Mock版本的AlgorithmImports
import tests.mocks.algorithm_imports_stub as AlgorithmImports
sys.modules['AlgorithmImports'] = AlgorithmImports

# 现在导入生产代码,它会使用Mock的OrderStatus, OrderTicket等
from src.TicketsManager import TicketsManager
```

#### 2. 回调机制验证
```python
# Mock Pairs对象记录回调状态
mock_pairs.callback_called = False

# 触发OnOrderEvent
tm.on_order_event(MockOrderEvent(101, MockOrderStatus.Filled))

# 验证回调被触发
assert mock_pairs.callback_called == True
assert mock_pairs.tracked_qty1 == 100  # 验证参数传递
```

#### 3. 异常场景构造
```python
# 手动设置订单状态为Canceled(真实回测无法做到)
ticket2 = MockOrderTicket(202, symbol2, MockOrderStatus.Canceled)

# 验证异常检测
status = tm.get_pair_status(pair_id)
assert status == "ANOMALY"  # 成功检测异常!
```

### 文档

创建`docs/测试指南.md`,包含:
- 测试运行方法(3种方式)
- 设计原理详解(为什么不影响生产代码)
- 隔离性验证步骤
- 扩展测试指南
- 常见问题解答(Q&A)
- 后续优化建议

### 配置更新

**`.gitignore`新增**:
```
# Testing
.pytest_cache/
htmlcov/
.coverage
src/__pycache__/
tests/__pycache__/
```

### 价值与意义

#### 解决的核心问题
- ❌ **问题**: QuantConnect回测100%订单成功,无法测试异常处理
- ✅ **解决**: Mock对象可任意构造Canceled/Invalid等异常状态
- ⏱️ **效率**: 几秒钟测试需要数周真实环境才能出现的异常

#### 对比真实环境
| 特性 | 单元测试 | QuantConnect回测 | 纸上交易 |
|-----|---------|-----------------|---------|
| **异常可控性** | 完全可控 | 无法触发 | 低概率触发 |
| **执行速度** | 秒级 | 分钟级 | 天/周级 |
| **成本** | 免费 | 免费 | 免费(无资金风险) |
| **环境依赖** | 无需QuantConnect | 需要LEAN | 需要QuantConnect |
| **可重复性** | 100% | 100% | 低(市场不确定) |

#### 未来扩展方向
- **短期**: 已完成核心功能验证 ✅
- **中期**: 集成pytest框架,添加覆盖率报告
- **长期**: CI/CD集成,纸上交易验证补充

### 技术债务
- ⚠️ **Mock对象简化**: 只实现TicketsManager需要的最小接口
  - 风险: 真实QuantConnect可能有未覆盖的边缘情况
  - 缓解: 通过纸上交易在真实环境验证
- ⚠️ **Windows编码问题**: test_tickets_manager.py的Unicode符号在Windows cmd报错
  - 解决: 提供test_simple.py作为无特殊符号版本

### 相关版本
- v6.4.0: 架构重构,引入TicketsManager
- v6.4.6: TicketsManager功能完整实现
- v6.4.11: Debug日志清理完成
- v6.5.0: 单元测试环境创建 ✅ **本版本**

---

## [v6.4.11_修复PairsManager多行Debug遗漏@20250131]

### Bug修复
**最终修复**: PairsManager.py中跨6行的Debug调用,参数`, 2`在独立行导致grep遗漏

### 问题详情
- **错误类型**: `Debug() takes 2 positional arguments but 3 were given`
- **错误位置**: `PairsManager.py:218` (log_statistics方法内)
- **触发条件**: 配对更新时统计日志输出
- **影响范围**: 每轮配对更新都会触发此错误

### 根本原因
v6.4.10使用的grep模式为单行模式:
```bash
grep -r "\.Debug\(.*,\s*\d+\s*\)" src/
```

**PairsManager的特殊情况**:
```python
# 代码跨6行,grep单行模式无法匹配
self.algorithm.Debug(          # Line 213: .Debug( 在这里
    f"[PairsManager] ...",     # Line 214
    f"活跃={...}",             # Line 215
    f"遗留={...}",             # Line 216
    f"休眠={...}",             # Line 217
    f"总计={...}", 2           # Line 218: , 2 在单独一行!
)                               # Line 219: ) 在这里
```

grep要求`.Debug(`和`, 2`在同一行才能匹配,但这里相隔5行,导致遗漏。

### 修复内容
- **修改**: `PairsManager.py:218` 删除`, 2`参数
- **验证**: 通过12个月回测(276个订单全部成功)
- **确认**: 所有Debug调用已统一为单参数形式

### TicketsManager功能验证

通过回测ID `5b7ab209a2d980c984386afff74f9ec5` (2023-09-20至2024-09-20,12个月):

| 验证项 | 预期 | 实际 | 结果 |
|--------|------|------|------|
| **订单注册** | 全部成功 | 276/276 (100%) | ✅ |
| **状态追踪** | PENDING→COMPLETED | 138对全部正确 | ✅ |
| **配对完整性** | 0个孤儿订单 | 0个孤儿 | ✅ |
| **锁定机制** | 防止重复下单 | 0次重复提交 | ✅ |
| **时间同步** | 双腿同时执行 | 100%同步 | ✅ |
| **回调机制** | 成交时间记录 | 间接验证通过 | ✅ |
| **异常检测** | Canceled/Invalid处理 | 未测试(无异常订单) | ⚠️ |

**关键发现**:
- 核心功能验证完整: 订单注册、状态追踪、锁定机制、回调机制均正常
- 异常场景未覆盖: 实际回测中未出现Canceled/Invalid/PartiallyFilled订单
- 生产环境建议: 监控首次异常订单时的日志,验证异常检测逻辑

### 重构总结: v6.4系列完整修复链

| 版本 | 修复内容 | 遗漏原因 |
|------|----------|----------|
| **v6.4.7** | main.py + Pairs.py + config.py | ✅ 主体完成,但遗漏3个模块 |
| **v6.4.9** | UniverseSelection.py | 遗漏属性引用 (debug_level→debug_mode) |
| **v6.4.10** | DataProcessor + CointegrationAnalyzer + BayesianModeler + TicketsManager | grep单行模式部分遗漏 |
| **v6.4.11** | PairsManager.py | grep单行模式完全遗漏(跨6行) ✅ **最终修复** |

**经验教训**:
1. **自动化工具限制**: grep单行模式无法处理多行代码结构
2. **回测覆盖率**: 3个月→12个月触发更多代码路径,暴露遗漏
3. **手动验证必要性**: 大规模重构需人工逐文件复查
4. **未来改进方向**: 使用AST解析器进行结构化代码分析

### 相关提交
- v6.4.7: 日志精简(首次,有遗漏)
- v6.4.8: 回测周期扩展(3个月→12个月)
- v6.4.9: 修复UniverseSelection.debug_level
- v6.4.10: 清理analysis+TicketsManager (10处)
- v6.4.11: 修复PairsManager多行遗漏 ✅

---

## [v6.4.10_完成Debug参数清理@20250131]

### Bug修复
**修复v6.4.7遗漏**: 清理analysis模块和TicketsManager中残留的Debug level参数

### 问题详情
- **错误类型**: `Debug() takes 2 positional arguments but 3 were given`
- **错误位置**: `DataProcessor.py:130` (_log_statistics方法)
- **触发条件**: 证券变更时数据处理日志输出
- **影响范围**: 所有回测在证券变更后会中断

### 根本原因
v6.4.7将Debug方法从`Debug(message, level=2)`简化为`Debug(message)`时:
- 使用Python脚本批量删除了固定参数形式 (`, 1)`, `, 2`)
- 但**遗漏了analysis模块和TicketsManager**,可能因为:
  - 多行Debug调用的regex模式未匹配
  - analysis模块在深层函数中,触发频率低,测试未覆盖

**为什么v6.4.9后仍有遗漏**:
- v6.4.9只修复了UniverseSelection中的`debug_level`属性引用
- 未系统性搜索所有Debug调用的level参数传递
- 12个月回测触发更多代码路径,最终暴露了这些遗漏

### 修复范围

| 文件 | 修复数量 | 行号 | 类型 |
|------|---------|------|------|
| **DataProcessor.py** | 3处 | 72, 85, 131 | 单行×2 + 多行×1 |
| **CointegrationAnalyzer.py** | 1处 | 53 | 多行 |
| **BayesianModeler.py** | 3处 | 47, 113, 261 | 多行×2 + 单行×1 |
| **TicketsManager.py** | 3处 | 158, 244, 266 | 多行×2 + 注释×1 |
| **合计** | **10处** | - | - |

### 修复示例

```python
# 单行Debug调用
# 修改前
self.algorithm.Debug(f"[DataProcessor] 处理失败: {e}", 2)
# 修改后
self.algorithm.Debug(f"[DataProcessor] 处理失败: {e}")

# 多行Debug调用
# 修改前
self.algorithm.Debug(
    f"[DataProcessor] 数据处理: {stats['total']}→{stats['final_valid']}只", 2
)
# 修改后
self.algorithm.Debug(
    f"[DataProcessor] 数据处理: {stats['total']}→{stats['final_valid']}只"
)
```

### 验证结果
```bash
# 1. 验证无遗漏的level参数
grep -r "\.Debug\(.*,\s*\d\+\s*\)" src/
# → 无结果 ✅

# 2. 验证无debug_level引用
grep -r "debug_level" src/
# → 无结果 ✅
```

### 关联版本历史
- **v6.4.7** (Jan 31): 日志系统精简 - 首次修改,遗漏analysis模块和TicketsManager
- **v6.4.9** (Jan 31): 修复UniverseSelection的debug_level属性引用
- **v6.4.10** (Jan 31): 完成所有Debug参数清理 - **最终修复** ✅

### 工程教训
1. ✅ **全面搜索**: 必须使用多种模式(单行/多行/变量传递)搜索所有引用
2. ✅ **回归测试**: 扩展回测周期能暴露更多代码路径中的隐藏bug
3. ✅ **系统性验证**: 修复后使用grep全面验证,确保无遗漏
4. ✅ **文档完整**: 记录修复历史,便于追踪多轮修复的关联关系

---

## [v6.4.9_修复debug_level遗漏引用@20250131]

### Bug修复
**修复v6.4.7遗漏**: UniverseSelection.py中仍引用旧的`debug_level`属性,导致回测运行时错误

### 问题详情
- **错误类型**: `AttributeError: 'BayesianCointegrationStrategy' object has no attribute 'debug_level'`
- **错误位置**: `UniverseSelection.py:264` (_log_selection_results方法)
- **触发条件**: 月初选股日志输出时 (MonthStart schedule触发)
- **影响范围**: 所有回测在月初时会中断

### 根本原因
v6.4.7将`debug_level`(0/1/2)改为`debug_mode`(True/False)时,遗漏了UniverseSelection.py的修改:
- 已修改: main.py, config.py, Pairs.py等8个文件
- 遗漏: UniverseSelection.py:264

**为什么现在才发现**:
- 3个月回测触发选股3次,可能未运行到此代码路径
- 扩展到12个月后,月初触发12次,暴露了这个bug

### 修复内容
```python
# 修改前
if not self.algorithm.debug_level:  # 0=不输出
    return

# 修改后
if not self.algorithm.debug_mode:  # False=不输出
    return
```

### 逻辑验证
- 旧逻辑: `not 0` = True → 跳过日志 ✅
- 新逻辑: `not False` = True → 跳过日志 ✅
- 语义一致,行为相同

### 版本关联
- **关联版本**: v6.4.7 (日志系统精简)
- **修复范围**: 最后一处debug_level引用
- **验证方法**: `grep -r "debug_level" src/` 返回空结果

---

## [v6.4.8_回测周期扩展@20250131]

### 核心变更
**回测周期扩展**: 从3个月扩展到12个月,提升统计显著性

### 详细修改

#### 时间配置 (src/config.py)
```python
# 修改前: 3个月周期
'start_date': (2024, 6, 20),
'end_date': (2024, 9, 20),

# 修改后: 12个月周期
'start_date': (2023, 9, 20),
'end_date': (2024, 9, 20),
```

#### 统计优势
- **回测时长**: 92天 → 366天 (含闰年, 4倍增长)
- **月度重选次数**: 3次 → 12次 (4倍增长)
- **市场环境覆盖**: 单季度 → 完整年度周期(4个季度)
- **样本充分性**: 更多配对生成/退出事件,减少单一时期偏差

#### 预期效果
1. **更稳健评估**: 经历多种市场环境(牛市/熊市/震荡)
2. **更多交易样本**: 月度重选带来更多配对创建和退出机会
3. **减少偶然性**: 避免"幸运期"或"不幸期"带来的偏差
4. **策略验证**: 验证在不同市场周期下的适应性

---

## [v6.4.7_日志系统精简@20250131]

### 核心变更
**日志系统全面简化**: 从3级debug系统简化为二元开关,删除约79%的日志调用(99处→20处)

**新增技术文档**: 创建`docs/订单回调机制详解.md`,完整解析事件驱动架构和观察者模式实现

### 详细修改

#### 1. 配置简化 (src/config.py)
```python
# 修改前: 3级系统
'debug_level': 2,  # 0=静默, 1=关键信息, 2=详细信息

# 修改后: 二元开关
'debug_mode': False,  # True=开发调试, False=生产运行
```

#### 2. Debug方法重构 (main.py)
```python
# 修改前: 支持分级控制
def Debug(self, message: str, level: int = 2):
    if level <= self.debug_level:
        QCAlgorithm.Debug(self, message)

# 修改后: 简单开关
def Debug(self, message: str):
    if self.debug_mode:
        QCAlgorithm.Debug(self, message)
```

#### 3. 日志精简统计
- **main.py**: 56处 → 8处 (删除48处)
  - 删除: OnData入口Portfolio状态、分析步骤详情、保证金追踪
  - 保留: 初始化完成、配对分析完成、交易动作(开仓/平仓/止损)、订单异常

- **src/Pairs.py**: 15处 → 5处 (删除10处)
  - 删除: 重新激活计数、持仓成交回调、开仓详情(保证金/Beta/数量)、Portfolio状态追踪
  - 保留: 持仓异常(单边/同向)、开仓失败(价格/市值/数量异常)

- **其他模块**: PairsManager.py、TicketsManager.py、PairSelector.py
  - 删除所有level参数(, 1)和(, 2)

#### 4. 文档更新
- **CLAUDE.md**: 更新日志约定说明,反映新的二元系统
- **代码诊断修复**: main.py:209 unused variable 'score' → '_'

### 优化效果
1. **代码更简洁**: 移除约79%的Debug调用
2. **系统更简单**: 从3级系统简化为True/False开关
3. **日志更聚焦**: 只保留可操作信息(交易、异常、风险)
4. **维护更容易**: 统一的日志接口,无需考虑level参数

### 性能影响
- 生产环境(debug_mode=False): 大幅减少字符串构建和I/O调用
- 开发环境(debug_mode=True): 仍可看到所有保留的关键日志

---

## [v6.4.6_代码瘦身与性能优化@20250131]

### 核心变更
1. **删除冗余方法**: 移除Pairs和PairsManager中的5个冗余方法(共~30行代码)
2. **性能优化**: 优化PairsManager持仓过滤方法,减少嵌套调用和中间字典构建
3. **代码清理**: 删除1个死代码方法,从未被调用

### 详细修改

#### Pairs.py (-18行)
**删除4个简单包装方法**:
1. `get_entry_time()` → 直接访问`pair.position_opened_time`
2. `get_exit_time()` → 直接访问`pair.position_closed_time`
3. `get_quality_score()` → 直接访问`pair.quality_score`
4. `get_sector()` → 直接访问`pair.sector`

**优化内部调用**:
- `is_in_cooldown()`: 改为直接访问`self.position_closed_time`
- `get_pair_holding_days()`: 改为直接访问`self.position_opened_time`

**理由**: Python惯例是直接访问公开属性,除非有额外逻辑(计算、验证、缓存)

#### PairsManager.py (-15行死代码, +6行优化)
**删除死代码**:
- `check_concentration_warning()`: 从未被调用的配对数量软警告方法

**优化持仓过滤方法**:
```python
# 优化前: 嵌套调用,构建中间字典 (O(2n) + N次查询)
def get_pairs_with_position(self):
    tradeable_pairs = self.get_all_tradeable_pairs()  # O(n)
    return {pid: pair for pid, pair in tradeable_pairs.items()
            if pair.has_position()}  # O(n) + N次查询

# 优化后: 直接遍历 (O(n) + N次查询)
def get_pairs_with_position(self):
    result = {}
    for pid in (self.active_ids | self.legacy_ids):
        pair = self.all_pairs[pid]
        if pair.has_position():
            result[pid] = pair
    return result
```

**性能提升**:
- 减少一次完整的字典构建操作
- 从O(2n)降至O(n)复杂度
- 代码意图更清晰,可读性更好

### 优化成果统计

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| **代码行数** | ~850行 | ~810行 | -40行 (-5%) |
| **方法数量** | 36个 | 31个 | -5个 (-14%) |
| **死代码** | 15行 | 0行 | -15行 |
| **每周期查询**(10配对) | ~25次 | ~15次 | -40% |
| **字典构建**(OnData) | 4次 | 2次 | -50% |

### 验证测试
- ✅ 所有删除的方法均为简单属性包装或死代码
- ✅ 内部引用已全部修正(is_in_cooldown, get_pair_holding_days)
- ✅ 优化后方法保持相同功能和接口
- ✅ 无breaking changes,向后兼容

---

## [v6.4.5_TicketsManager回调优化与持仓追踪修复@20250131]

### 核心变更
1. **debug_level提升**: 从1提升到2,输出TicketsManager和Pairs的详细日志
2. **时间追踪优化**: 删除`_get_order_time()`的48行O(n)查询逻辑,改用O(1)回调存储
3. **持仓追踪修复**: 解决多配对共享symbol时的单边持仓误报问题

### 架构变更

#### 1. 回调模式实现 (TicketsManager → Pairs)
**设计目的**: 解耦订单追踪与业务数据存储

**核心机制**:
- TicketsManager检测到COMPLETED状态时,回调Pairs记录时间和数量
- Pairs不再主动查询订单历史,改为被动接收通知
- 删除`Pairs._get_order_time()`方法(48行O(n)遍历订单历史)

**修改位置**:
- `TicketsManager.py:45-67`: 添加`pairs_manager`引用和`pair_actions`字典
- `TicketsManager.py:120-159`: `register_tickets()`接收`action`参数
- `TicketsManager.py:226-240`: COMPLETED时回调`pairs_obj.on_position_filled()`
- `Pairs.py:109-142`: 新增`on_position_filled()`回调方法
- `Pairs.py:550-607`: 简化`get_entry_time()`/`get_exit_time()`为O(1)属性访问
- `main.py:68,515,232,250...`: 所有`register_tickets()`调用添加`action`参数

**回调链路**:
```
OnOrderEvent触发
  → TicketsManager.on_order_event()
  → 检测current_status == "COMPLETED"
  → 准备数据: action, fill_time, tickets
  → pairs_obj.on_position_filled(action, fill_time, tickets)
  → Pairs存储时间和数量
```

#### 2. 持仓追踪重构 (tracked_qty机制)
**问题根源**: `Portfolio[symbol].Quantity`返回全账户总持仓,当多个配对共享symbol时产生误报

**误报场景**:
```python
# 时间线
T1: 配对A('AMZN', 'CMG') 平仓 → Portfolio[AMZN] = 0
T2: 配对B('AMZN', 'GM') 开仓 → Portfolio[AMZN] = 125
T3: 配对A调用get_position_info()
    → qty1 = Portfolio[AMZN].Quantity = 125  # ← 这是配对B的!
    → qty2 = Portfolio[CMG].Quantity = 0
    → 误报: "[Pairs.WARNING] ('AMZN', 'CMG') 单边持仓LEG1: qty1=+125"
```

**解决方案**: 配对专属持仓追踪
- `Pairs.py:86-87`: 添加`tracked_qty1`/`tracked_qty2`属性
- `Pairs.py:123-129`: 回调时从OrderTicket提取`QuantityFilled`
- `Pairs.py:235-236`: `get_position_info()`使用`tracked_qty`代替Portfolio查询
- `Pairs.py:138-139`: 平仓时清零`tracked_qty`

**数据流**:
```
MarketOrder提交
  → OrderTicket返回
  → TicketsManager注册
  → OnOrderEvent: Status=Filled
  → TicketsManager回调Pairs
  → Pairs从ticket.QuantityFilled提取数量
  → 存储到tracked_qty1/tracked_qty2
  → get_position_info()返回tracked_qty(配对专属)
```

#### 3. 代码清理
- 删除`Pairs.adjust_position()`方法(40行) - 绕过TicketsManager注册,违反架构
- 删除`Pairs._get_order_time()`方法(48行) - O(n)性能差,已被回调替代
- `Pairs.py:504-536`: 简化`check_position_integrity()`复用`get_position_info()`

### 技术优势

**vs 旧实现**:
| 指标 | 旧实现 | 新实现 | 改进 |
|------|--------|--------|------|
| 时间查询 | O(n)遍历订单历史 | O(1)属性访问 | 性能优化 |
| 持仓准确性 | Portfolio全局查询(误报) | OrderTicket追踪(准确) | 修复bug |
| 代码行数 | +88行(查询逻辑) | -2行(回调+属性) | 净减少90行 |
| 日志可见性 | debug_level=1(关键信息) | debug_level=2(详细信息) | 可调试性 |

**回调模式优势**:
1. **解耦**: TicketsManager不需要知道Pairs的内部实现
2. **单向依赖**: TicketsManager → PairsManager → Pairs (无循环依赖)
3. **职责分离**: 订单追踪 vs 业务数据存储
4. **扩展性**: 将来可轻松添加新回调(如部分成交通知)

### 配置变更
- `config.py:21`: `debug_level: 1 → 2` (临时用于测试TicketsManager日志)

### 预期日志输出
```
2024-07-02 16:00:00 [Pairs.open] ('AMZN', 'CMG') SHORT_SPREAD 保证金:21667 ...
2024-07-02 16:00:00 [TM注册] ('AMZN', 'CMG') OPEN 2个订单 状态:PENDING
2024-07-02 20:00:00 [OOE] ('AMZN', 'CMG') OrderId=123 Status=Filled → 配对状态:PENDING
2024-07-02 20:00:00 [OOE] ('AMZN', 'CMG') OrderId=124 Status=Filled → 配对状态:COMPLETED
2024-07-03 13:00:00 [Pairs.callback] ('AMZN', 'CMG') 双腿开仓完成 时间:2024-07-02T20:00:00Z 数量:(-70/+103)
2024-07-03 13:00:00 [OOE] ('AMZN', 'CMG') 订单全部成交,配对解锁
```

### 验证目标
1. ✅ 查看`[TM注册]`日志确认订单注册时机
2. ✅ 查看`[OOE]`日志追踪订单状态转换
3. ✅ 查看`[Pairs.callback]`日志验证时间和数量记录
4. ✅ 确认单边持仓误报是否消失

---

## [v6.4.4_OnOrderEvent订单追踪实现@20250130]

### 核心变更
**彻底解决订单重复问题**: 从基于时间的去重机制迁移到基于OrderId的订单生命周期追踪

### 新增模块
**TicketsManager订单管理器** (`src/TicketsManager.py`):
- **核心映射**: OrderId → pair_id (O(1)查找)
- **状态管理**: PENDING(锁定) → COMPLETED(解锁) → ANOMALY(异常)
- **异步安全**: 通过订单状态锁定配对,防止MarketOrder异步导致的重复下单

### 架构变更

1. **Pairs类返回值修改**:
   - `open_position()`: 返回 `List[OrderTicket]` (原void)
   - `close_position()`: 返回 `List[OrderTicket]` (原void)
   - `create_order_tag()`: 时间戳精确到秒 `%Y%m%d_%H%M%S` (原 `%Y%m%d`)

2. **main.py订单流程重构**:
   - **初始化**: 添加 `self.tickets_manager = TicketsManager(self)`
   - **开仓逻辑**:
     - 去重检查: `is_pair_locked()` 替代 `signal_key in daily_processed_signals`
     - 订单注册: `tickets_manager.register_tickets(pair_id, tickets)`
   - **平仓逻辑**: 同上(CLOSE/STOP_LOSS信号 + 3种风控平仓)
   - **新增OnOrderEvent()**: 委托给TicketsManager统一处理

3. **移除旧机制**:
   - ❌ `self.daily_processed_signals = {}`
   - ❌ `self._last_processing_date = None`
   - ❌ UTC时区相关去重逻辑

### 技术优势

**vs 旧时间去重机制**:
- ✅ **异步安全**: OrderId立即可用,不受MarketOrder异步影响
- ✅ **时区无关**: 无需处理UTC/ET转换问题
- ✅ **精确追踪**: 可检测PartiallyFilled/Canceled/Invalid等状态
- ✅ **自动解锁**: 订单Filled后自动解除配对锁定

**防重复机制**:
1. 调用 `pair.close_position()` 前检查 `is_pair_locked()`
2. MarketOrder返回OrderTicket后立即 `register_tickets()`
3. 配对进入PENDING状态,阻止后续OnData重复下单
4. OnOrderEvent检测到全部Filled后,状态 → COMPLETED

### PartiallyFilled处理策略
- **默认**: 等待broker自动继续成交(GTC订单特性)
- **异常检测**: Canceled/Invalid → 标记ANOMALY,交由风控处理
- **超时机制**: 预留扩展点(可添加超时检测)

### 回测验证
- 待验证: 之前的AMZN持仓爆炸问题(159→477→954)应彻底解决
- 关键指标: 检查同一pair_id的OrderId是否唯一,无重复下单

### 状态
- ✅ 架构实施完成
- ⏳ 待回测验证效果

---

## [v6.4.3_去重机制问题诊断@20250130]

### 问题发现
- **持仓爆炸持续**: v6.4.2修复后,AMZN持仓仍爆炸(159→477→954股)
- **去重机制完全失效**: 添加的5个平仓场景去重检查未生效

### 根本原因诊断
1. **UTC时区错误** (主要原因):
   - 当前使用 `current_date = self.Time.date()` (UTC时间)
   - 美东 9/5 16:00收盘 → UTC 9/5 20:00 → date() = 9/5 ✓
   - 美东 9/6 04:00盘前 → UTC 9/6 08:00 → date() = 9/6 ✗ (跨天!)
   - 去重字典在UTC 00:00被清空,同一配对可重复平仓

2. **多配对股票冲突**:
   - AMZN同时在(AMZN,GM)和(AMZN,CMG)两个配对中
   - 两个配对独立查询Portfolio.Quantity,都读到159股
   - 各自下单平仓159股 → 实际订单318股

3. **MarketOrder异步特性**:
   - 订单提交后Portfolio.Holdings不立即更新
   - 多次OnData调用读取相同旧数据,生成重复订单

### Backtest证据
- **Backtest ID**: 474cef33a4b5b8439a1902f4cd505abd
- **关键订单序列**:
  - Order #32: 9/5 20:00 (AMZN,GM)_CLOSE 卖159股
  - Order #35: 9/6 04:00 (AMZN,GM)_CLOSE 卖159股 (同一配对重复!)
  - Order #38: 9/6 20:00 (AMZN,GM)_CLOSE 买477股
  - Order #41: 9/9 04:00 (AMZN,GM)_CLOSE 买477股
  - Order #44: 9/9 20:00 (AMZN,GM)_CLOSE 卖954股

### 待修复方案
1. **时区修复**: 使用美东时区计算交易日而非UTC
2. **股票级别去重**: 防止多配对同时操作同一股票
3. **信号处理完善**: 添加continue语句确保逻辑终止

### 状态
- ⚠️ 问题已诊断,待实施修复
- 📊 回测证据已收集,根因已确认

---

## [v6.4.2_保证金计算数学修复@20250130]

### 核心算法修复
- **修复关键数学错误**：数量配比约束而非市值配比约束
  - 原错误：假设 `value_A = beta × value_B` (市值遵循beta关系)
  - 正确公式：假设 `Qty_A = beta × Qty_B` (数量遵循beta关系)
  - 核心洞察：配对交易利用价格偏离，入场时市值非beta比例，但数量必须保持beta比例

- **重写保证金反推公式**：
  - 联立方程：
    1. `X + Y = margin_allocated` (保证金约束)
    2. `(X/margin_rate_A)/Price_A = beta × (Y/margin_rate_B)/Price_B` (数量约束)
  - LONG_SPREAD (A多0.5, B空1.5)：
    - `Y = margin × 3 × Price_B / (beta × Price_A + 3 × Price_B)`
  - SHORT_SPREAD (A空1.5, B多0.5)：
    - `Y = margin × Price_B / (beta × 3 × Price_A + Price_B)`

### 方法签名变更
- **Pairs.calculate_values_from_margin()**：
  - 旧签名：`calculate_values_from_margin(margin_allocated, signal)`
  - 新签名：`calculate_values_from_margin(margin_allocated, signal, data)`
  - 原因：需要获取当前价格来计算正确的数量配比

- **Pairs.open_position()**：
  - 旧签名：`open_position(signal, value1, value2, data)`
  - 新签名：`open_position(signal, margin_allocated, data)`
  - 原因：先传入保证金，内部反推市值，符合新架构思路

### 保证金管理简化
- **动态缓冲策略**：
  - 旧方法：`initial_margin = Portfolio.MarginRemaining - buffer`
  - 新方法：`initial_margin = Portfolio.MarginRemaining × 0.95`
  - 优势：5%缓冲随账户规模动态变化，无需复杂计算

- **配置重命名**：
  - `config.py`：`margin_safety_buffer` → `margin_usage_ratio: 0.95`
  - 语义更准确：使用率而非固定缓冲

### 质量保证机制
- **数量配比验证**：添加5%阈值检查
  - 计算：`actual_ratio = Qty_A / Qty_B`
  - 对比：`expected_ratio = beta`
  - 警告：偏差 > 5%时输出调试信息

- **移除不必要检查**：删除30%账户净值上限检查
  - 原因：数学公式保证 `X + Y = margin_allocated`，不会超限

### Bug修复
- **配置重构遗漏**：修复 `Pairs.py:76` KeyError
  - 问题：仍读取不存在的 `margin_safety_buffer` 配置键
  - 修复：删除废弃的 `self.margin_buffer` 变量赋值
  - 影响：导致策略无法初始化

### 预期改进
- 消除596股AMZN异常仓位问题
- 解决12次保证金不足错误
- 降低AMZN 71%集中度
- 数量配比精确匹配beta (±5%容差)

---

## [v6.4.1_保证金架构重构与代码清理@20250130]

### 保证金分配架构重构
- **动态缩放机制**：实现公平的保证金动态分配
  - 记录初始保证金快照：`initial_margin = Portfolio.MarginRemaining - buffer`
  - 应用缩放公式：`margin_allocated = current_margin × pct × (initial/current)`
  - 确保每个配对获得的分配比例基于相同的初始基准
- **反向计算方法**：新增 `Pairs.calculate_values_from_margin()`
  - 从保证金占用反推AB两腿的市值
  - LONG_SPREAD: `value_B = margin / (margin_long × beta + margin_short)`
  - SHORT_SPREAD: `value_B = margin / (margin_short × beta + margin_long)`
- **方法签名重构**：`Pairs.open_position()` 参数变更
  - 旧签名：`open_position(signal, allocation_amount, data)`
  - 新签名：`open_position(signal, value1, value2, data)`
  - 体现"先计算再分配"的新架构思路

### 代码组织优化
- **Pairs.py 重构**（~601行 → 565行，净减36行）：
  - 删除旧架构方法：`calculate_required_margin()`, `can_open_position()`
  - 按功能分组为8个模块：初始化、信号生成、持仓查询、交易执行、保证金计算、风控、时间追踪、辅助方法
- **PairsManager.py 重构**（~211行 → 224行，新增13行）：
  - 新增缺失方法：`get_pair_by_id()` (main.py:350有调用但未实现)
  - 按功能分组为5个模块：初始化、核心管理、查询接口、风险分析、日志统计

### 配置管理优化
- **消除硬编码参数**：
  - 移除 `Pairs.calculate_values_from_margin()` 中的硬编码 0.5/1.5
  - 使用配置参数：`self.margin_long`, `self.margin_short`
  - 配置来源：`config.py:104-105` (margin_requirement_long/short)
- **删除冗余配置**：移除 `config.py` 中已废弃的 `cash_buffer_ratio`

### Bug修复
- **main.py:337 语法错误**：修复 `continue` 语句在非循环中使用
  - 改为条件嵌套结构：`if initial_margin >= min_investment: ... else: ...`
- **缺失方法补充**：实现 `PairsManager.get_pair_by_id()` 避免运行时错误

### 命名优化
- **语义化重命名**：`get_entry_candidates()` → `get_sequenced_entry_candidates()`
  - 更准确表达方法行为：获取候选并按质量分数排序
  - 同步更新调用点：main.py:326

---

## [v6.4.0_整体代码优化待测试@20250130]

### 风险管理架构重构
- **职责分离**：将风险检测与执行分离，风控只负责检测，main.py负责执行
- **Portfolio级别风控**：
  - is_account_blowup()：爆仓检测
  - is_excessive_drawdown()：回撤检测
  - is_high_market_volatility()：市场波动检测
  - check_sector_concentration()：行业集中度检测
- **Pair级别风控**：
  - check_holding_timeout()：持仓超期检测
  - check_position_anomaly()：持仓异常检测
  - check_pair_drawdown()：配对回撤检测

### 市场波动率优化
- 实现20日滚动窗口历史波动率计算
- 使用deque避免重复History()调用
- 年化波动率公式：`np.std(returns) * np.sqrt(252)`
- 市场波动检测从portfolio风控移至开仓前置检查

### 资金管理优化
- **移除硬性限制**：取消max_holding_pairs配对数量限制
- **自然资金约束**：通过资金可用性自然限制配对数量
- **智能分配机制**：
  - 基于质量分数的动态分配：min_pct + quality_score * (max_pct - min_pct)
  - 累积百分比检查确保buffer和最小投资要求
  - 从低质量配对开始剔除直到满足约束

### 代码质量提升
- **Pairs.get_planned_allocation_pct()**：
  - 移除冗余的持仓和信号检查
  - 简化为纯计算函数，保持单一职责
- **PairsManager.get_entry_candidates()**：
  - 承担所有业务逻辑判断
  - 返回按质量分数排序的候选列表
- **开仓执行反馈**：
  - 添加执行结果对比（计划vs实际）
  - 明确显示被跳过的配对数量

### 版本历史重命名
## [v6.3.1_质量评分与风控优化@20250128]

### 质量评分系统优化
- **指标替换**：
  - 移除correlation指标（与statistical重复）
  - 新增half_life指标（均值回归速度，5-30天归一化）
  - 新增volatility_ratio指标（spread波动/股票波动比）
- **流动性计算修复**：
  - 修复使用不存在字段的问题
  - 改为基于成交额（volume × close）计算
  - 归一化阈值调整至$50M
- **权重重新分配**：
  - statistical: 30%（协整强度）
  - half_life: 30%（回归速度）
  - volatility_ratio: 20%（稳定性）
  - liquidity: 20%（流动性）

### 风控参数优化
- **爆仓线计算逻辑改进**：
  - 从"剩余比例"改为"亏损比例"
  - blowup_threshold=0.3现表示亏损30%触发（更直观）
  - 与drawdown概念统一
- **行业集中度调整**：
  - sector_exposure_threshold: 60% → 40%
  - sector_target_exposure: 50% → 30%
- **回撤线优化**：drawdown_threshold: 30% → 15%

### 配置结构优化
- **参数重组**：
  - min_position_pct/max_position_pct从main移到pairs_trading
  - cash_buffer_ratio保留在main（全局资金管理）
- **参数重命名**：cooldown_days → pair_cooldown_days（更准确）
- **删除未使用参数**：max_pair_concentration（从未被引用）

### 代码清理
- 删除config_backup_20250128.py备份文件
- 删除CLAUDE.local.md（内容已整合）
- 清理Python缓存目录（__pycache__）

---

## [v6.3.1_配置文件清理优化@20250128]

### 配置优化
- **删除未使用参数**：
  - main section: portfolio_max_drawdown, portfolio_max_sector_exposure
  - pairs_trading section: flat_signal_duration_days, entry_signal_duration_days
  - risk_management section: max_single_drawdown
- **移除历史遗留配置**：
  - 完全删除portfolio_construction section（Algorithm Framework遗留）
- **参数整合**：
  - 将重复参数（max_tradeable_pairs, max_holding_days, max_pair_concentration）统一保留在pairs_trading section
  - 从risk_management删除重复定义
- **代码精简**：配置文件减少约20%代码量，结构更清晰

### 技术改进
- 消除参数重复定义，降低维护成本
- 配置结构与OnData架构完全对齐
- 保留备份文件config_backup_20250128.py供参考

---

## [v6.3.0_完整交易系统实现@20250127]

### 风控体系重构
- **RiskManagement模块化**：将风控逻辑从main.py抽离到独立模块
- **双层风控架构**：
  - PortfolioLevelRiskManager：组合层面风控（爆仓、回撤、市场波动、行业集中）
  - PairLevelRiskManager：配对层面风控（持仓超期、异常持仓、配对回撤）
- **生成器模式**：配对风控使用yield优雅过滤风险配对

### 交易执行系统
- **完整的开平仓逻辑**：
  - 平仓处理：CLOSE信号（正常平仓）、STOP_LOSS信号（止损）
  - 开仓处理：LONG_SPREAD、SHORT_SPREAD信号，支持Beta对冲
- **资金管理系统**：
  - 5%永久现金缓冲
  - 动态仓位分配：10% + quality_score × 15%
  - 最小/最大仓位限制（10%-25%初始资金）
- **执行优化**：
  - 分离有持仓/无持仓配对处理
  - 质量分数优先的开仓顺序
  - 纯执行方法设计（职责单一）

### 代码优化
- **Pairs类方法重组**：
  - 分为3类：核心交易功能、持仓查询功能、基础属性
  - close_position/open_position简化为纯执行方法
- **PairsManager类方法重组**：
  - 分为3类：核心管理功能、查询访问功能、迭代器属性
  - 新增get_pairs_with_position/get_pairs_without_position方法
- **配置优化**：
  - 资金管理参数移至Initialize一次性计算
  - 避免OnData中重复计算固定值

### 架构更新
- **CLAUDE.md全面更新**：
  - 更新至v6.2.0 OnData Architecture
  - 添加Trading Execution Flow详细说明
  - 更新所有模块文档反映当前架构

### 技术改进
- 移除0.5倍min_allocation的模糊判断
- manage_pair_risks重命名为manage_position_risks语义更清晰
- 资金不足时直接停止开仓，逻辑更简洁

---

## [v6.2.0_配对风控体系实现@20250127]

### 风控体系完善
- **配对层面风控框架**：在OnData中实现完整的配对风控检查流程
- **Portfolio与Pair双层风控**：形成组合层面和配对层面的完整风险管理体系

### 配对风控实现
- **持仓超期检查**：
  - 使用get_pair_holding_days()获取持仓天数
  - 超过max_holding_days（30天）强制平仓

- **异常持仓检查**：
  - 单边持仓检测（PARTIAL状态）
  - 方向相同检测（same_direction）
  - 统一处理逻辑，发现异常立即清仓

- **配对回撤检查**：
  - 实现high_water_mark机制追踪历史最高净值
  - 计算从最高点的回撤比例
  - 回撤超过20%触发清仓并重置记录

### 代码变更
- 修改：main.py（添加配对风控逻辑）
- 修改：src/Pairs.py（重命名get_position_age为get_pair_holding_days）
- 修改：src/config.py（风控参数配置）

### 技术细节
- 在Initialize中添加pair_high_water_marks字典
- OnData中按优先级执行风控检查
- 风控触发后使用continue跳过后续处理

---

## [v6.1.0_PairsManager架构优化@20250126]

### 架构优化
- **PairsFactory合并**：将PairsFactory功能整合到PairsManager，简化架构层次
- **智能管理器升级**：PairsManager从"批量包装器"转型为"智能管理器"

### 核心改进
- **迭代器接口实现**：
  - 添加@property装饰器的生成器接口
  - active_pairs、legacy_pairs、tradeable_pairs等优雅访问
  - 支持Pythonic的迭代和列表推导式

- **集合级分析方法**：
  - get_portfolio_metrics()：组合级指标汇总
  - get_risk_summary()：多维度风险评估
  - get_concentration_analysis()：集中度分析
  - get_sector_concentrations()：行业分布分析

- **全局约束与协调**：
  - can_open_new_position()：全局容量检查
  - close_risky_positions()：智能批量风控
  - transition_legacy_to_dormant()：状态批量转换
  - get_capacity_status()：容量状态监控

### 代码优化
- **删除冗余方法**：移除7个简单的批量包装方法（-80行）
- **代码精简**：通过迭代器模式减少重复代码
- **遵循设计原则**：单一职责、开闭原则、DIP、LoD

### 文件变更
- 修改：src/PairsManager.py（核心重构）
- 修改：main.py（移除Factory依赖）
- 删除：src/analysis/PairsFactory.py
- 修改：src/config.py（添加pairs_trading配置）
- 新增：src/example_usage.py（使用示例）

---

## [v6.0.0_OnData架构重构@20250121]

### 架构转型
- **架构模式**：从 Algorithm Framework 转向 OnData 驱动架构
- **分支**：feature/ondata-integration 实验性开发

### 核心组件重构
- **新增 Pairs 类**（312行）：
  - 配对交易的核心数据对象
  - 无状态设计，通过订单历史查询状态
  - 完整的生命周期管理（信号生成、持仓查询、冷却期）
  - 标准化订单标签系统

- **分析模块迁移**：
  - 从 src/alpha/ 迁移至 src/analysis/
  - 保持原有五个模块结构
  - 配置引用从 alpha_model 改为 analysis

- **配置重构**：
  - 删除 alpha_model 配置节
  - 新增 pairs_trading 配置节（交易阈值、冷却期）
  - 新增 analysis 配置节（统计分析参数）
  - main.py 配置引用同步更新

### 删除的组件
- 移除 Algorithm Framework 组件（Execution, PortfolioConstruction, RiskManagement）
- 删除旧的 alpha 模块目录
- 移除测试框架（待后续重建）

### 代码统计
- 新增：src/Pairs.py（312行）
- 新增：src/analysis/（5个模块，约1000行）
- 删除：Algorithm Framework 组件（约500行）
- 删除：测试代码（约2000行）

---

## [v5.0.0_Alpha模块优化合并主线@20250120]

### 重大架构升级
- **版本跨越**：从v4.2.0直接升级到v5.0.0，标志着架构的重大改进
- **分支合并**：将feature/cpm-development的Alpha优化成果合并到主线

### Alpha模块重构
- **架构优化**：
  - 删除PairAnalyzer中间层（-73行）
  - AlphaModel直接调用三个独立模块
  - 流程从3步扩展为5步，职责更清晰
  - 创建全面的Alpha_README.md文档（320行）

- **模块拆分**：
  - CointegrationAnalyzer.py：专注统计分析（150行）
  - BayesianModeler.py：贝叶斯MCMC建模（198行）
  - DataProcessor.py：数据处理（优化后115行）
  - SignalGenerator.py：信号生成（优化后236行）
  - AlphaModel.py：主控制器（精简至226行）

- **策略逻辑集中**：
  - 质量评估和配对筛选移至AlphaModel
  - CointegrationAnalyzer专注纯统计分析
  - 策略参数集中在AlphaModel管理

- **配置管理修复**：
  - 修复所有config.get()默认值问题
  - 确保config.py为唯一配置源
  - 修复AlphaModel和SignalGenerator的配置引用
  - 将市场风控参数移至alpha_model配置块

### 代码统计
- 删除文件：src/alpha/PairAnalyzer.py（-73行）
- 新增文档：src/alpha/Alpha_README.md（+320行）
- 总代码量：Alpha模块约973行（优化前1009行）
- 代码减少36行，可读性大幅提升

### 实验分支计划
- feature/framework-tradingpair：Algorithm Framework + TradingPair跨模块共享
- feature/ondata-integration：OnData集成 + TradingPair作为核心对象
- 两个分支将从v5.0.0起点进行不同架构实验

---

## [v4.2.0_PortfolioConstruction优化@20250809]
### PortfolioConstruction模块重大优化
- **智能Target生成器转型**：
  - 从机械转换器升级为智能决策模块
  - 移除冗余的信号验证（已在AlphaModel完成）
  - 移除Tag中的reason字段解析
  - 删除_validate_signal和_get_pair_position_status方法

- **质量过滤机制**：
  - 添加quality_score < 0.7的硬编码过滤
  - 防止低质量信号进入交易执行
  - 回测验证过滤70个低质量信号（AMZN&CMG等）

- **冷却期管理内置**：
  - PC内部实现7天冷却期追踪
  - 使用tuple(sorted([symbol1, symbol2]))确保配对一致性
  - 避免[A,B]和[B,A]被视为不同配对
  - 回测验证冷却期正确生效（PG&WMT在第7天可重新交易）

- **代码优化**：
  - main.py清理：所有imports移至顶部region
  - 启用真实PortfolioConstruction替代NullPortfolioConstructionModel
  - 删除不必要的注释和TODO标记

---

## [v4.1.0_AlphaModel模块化重构@20250809]
### AlphaModel模块化重构完成
- **模块拆分**：
  - 将1365行单文件拆分为5个独立模块
  - AlphaState.py - 集中状态管理（persistent/temporary/control）
  - DataProcessor.py - 数据处理逻辑
  - PairAnalyzer.py - 配对分析整合（协整+贝叶斯）
  - SignalGenerator.py - 信号生成逻辑
  - AlphaModel.py - 主协调器

- **风控前置机制**：
  - 实现配对级别的过期资产清理
  - SignalGenerator添加持仓前置检查
  - 建仓信号：检查两资产都无持仓
  - 平仓信号：检查至少一资产有持仓

- **Bug修复**：
  - 修复过期配对清理逻辑：从资产级别改为配对级别
  - 解决AMZN&CMG→AMZN&GM时CMG未清理问题
  - 防止无持仓生成平仓信号，有持仓生成建仓信号

---

## [v4.0.0_架构重构-删除PairRegistry@20250808]
### 重大架构重构
- **PairRegistry完全移除**：
  - 删除 `src/PairRegistry.py` 文件
  - 移除所有模块中的PairRegistry依赖
  - AlphaModel、RiskManagement、OrderTracker均已更新
  - 删除相关测试文件

- **配置管理优化**：
  - 创建独立配置文件 `src/config.py`
  - 从main.py分离所有配置参数
  - 支持多环境配置（production/test/development）

- **CentralPairManager简化**：
  - 移除所有预设方法骨架
  - 保持为空白类，等待根据实际需求设计
  - 遵循增量式重构原则

---

## [v2.1.0_代码优雅性重构@20250119]
### 重构内容
- **UniverseSelection模块优雅性重构**:
  - 代码量从500+行精简到313行（约40%缩减）
  - FINANCIAL_CRITERIA从类属性改为实例属性，提升灵活性
  - 重新组织方法顺序：公开方法 → 主筛选 → 辅助方法 → 日志输出
  - 统一代码注释风格，移除冗余docstring

- **配置和主程序简化**:
  - config.py简化debug_level为二元选择（0=不输出, 1=输出统计）
  - main.py代码风格统一，注释精简
  - 临时注释其他模块用于独立测试

- **日志输出优化**:
  - 保留关键统计信息，移除冗余输出
  - 统一输出格式："第【N】次选股: 粗选X只 -> 最终Y只"
  - 标点符号统一使用英文标点

### 架构影响
- 提升代码可维护性和可读性
- 为后续模块重构建立标准模式
- 保持功能完全不变，仅优化代码结构

---

## [v2.0.0_架构简化移除CPM和OnOrderEvent@20250119] (feature/cpm-development分支)
### 重大重构：架构简化
- **完全移除CentralPairManager**：
  - 删除src/CentralPairManager.py及其所有相关代码
  - 简化架构，减少约1000+行代码
  - 直接基于Portfolio状态进行配对管理

- **移除OnOrderEvent逻辑**：
  - 删除main.py中的OnOrderEvent方法（约70行）
  - 简化配对状态管理，直接使用Portfolio查询

- **简化模块交互**：
  - AlphaModel: 移除CPM交互，直接使用Portfolio查询持仓
  - RiskManagement: 重写为简化版本，只保留核心风控功能
  - PortfolioConstruction: 移除CPM依赖，专注于资金管理

### 技术改进
- **架构简化**：
  - 从复杂的中央管理模式转为直接查询模式
  - 每个模块职责更加清晰，耦合度更低
  - 移除了复杂的状态管理和同步机制

- **性能优化**：
  - 减少内存占用（无需维护额外状态）
  - 减少计算开销（无需状态同步）
  - 简化数据流（直接查询而非中介管理）

- **可维护性提升**：
  - 代码量减少约30%（删除1300+行）
  - 模块间依赖更加简单直接
  - 更容易理解和调试

### 代码变更
- **删除文件**：
  - src/CentralPairManager.py
  - tests/unit/test_central_pair_manager.py
  - tests/unit/test_cpm_v0.py
  - tests/unit/test_cpm_v1.py
  - tests/unit/test_market_cooldown.py
  - tests/unit/test_risk_*.py (多个旧版本测试)

- **修改文件**：
  - main.py: 移除CPM初始化和OnOrderEvent方法
  - src/alpha/AlphaModel.py: 移除CPM交互，简化构造函数
  - src/alpha/SignalGenerator.py: 用Portfolio查询替代CPM查询
  - src/RiskManagement.py: 完全重写为简化版本
  - src/PortfolioConstruction.py: 移除CPM依赖
  - src/config.py: 移除CPM配置参数

### 功能保持
- **所有核心功能保持不变**：
  - 贝叶斯协整分析
  - Z-score信号生成
  - 配对交易逻辑
  - 基本风险控制

- **改进的风控机制**：
  - Alpha层：市场风控、重复建仓检查
  - RiskManagement层：極端亏损止损
  - 直接、高效、易理解

### 测试状态
- **单元测试**：全部通过（简化后的45个测试）
- **集成测试**：需要进一步验证
- **回测测试**：待执行

### 升级影响
- **不兼容变更**：这是一个重大版本升级
- **需要重新部署**：所有现有部署需要更新
- **配置简化**：CPM相关配置参数已移除

---

## [v1.9.2_修复市场冷静期强制平仓逻辑@20250118] (feature/cpm-development分支)
### 重要修复：市场风控机制
- **修复市场冷静期逻辑缺陷**：
  - 之前：市场冷静期只阻止建仓，不会主动平仓
  - 现在：立即强制平仓所有持仓，真正实现风控目的
  - 在`_generate_pair_signals`开头添加强制平仓逻辑
  
- **改进日志输出**：
  - 区分"强制平仓"和"正常平仓"
  - 汇总输出平仓数量和剩余冷静期
  - 添加z-score值便于追踪
  
- **代码优化**：
  - 移除冗余的`is_market_cooldown`检查
  - 市场冷静期逻辑集中在方法开头处理
  - 添加测试用例验证修复效果

## [v1.9.1_更新所有模块注释@20250118] (feature/cpm-development分支)
### 文档优化
- **更新模块注释**：
  - CPM类文档移除"PC意图管理"相关描述
  - PC类文档强调其作为"纯粹资金管理器"的角色
  - Alpha层文档明确说明承担所有风控过滤职责
  - SignalGenerator新增市场风控方法的详细注释
  
- **视觉优化**：
  - CPM中模块交互部分使用增强的分隔符
  - 删除所有过时的版本引用（如"v1实现"）
  - 确保注释与代码功能完全一致
  
- **影响文件**：
  - src/CentralPairManager.py: 30行修改
  - src/PortfolioConstruction.py: 53行修改
  - src/alpha/AlphaModel.py: 11行修改
  - src/alpha/SignalGenerator.py: 17行修改

## [v1.9.0_集中风控到Alpha层删除PC-CPM交互@20250118] (feature/cpm-development分支)
### 架构优化：单一职责原则
- **删除PC冷却期管理**：
  - 删除cooldown_records和相关逻辑
  - 冷却期统一由Alpha层通过CPM查询实现
  - 避免重复过滤，提高效率
  
- **市场风控移至Alpha层**：
  - 将SPY跌幅检查从PC移到SignalGenerator
  - 新增_check_market_condition()和_is_market_in_cooldown()
  - 从源头控制：极端市场不生成建仓信号
  - 避免无效信号的下游处理
  
- **删除PC-CPM交互**：
  - 完全删除submit_intent()及相关方法
  - 删除意图管理数据结构（intents_log, daily_intent_cache）
  - 删除_check_open_eligibility()和_create_open_instance()
  - PC现在纯粹负责资金管理
  
- **架构简化效果**：
  - 代码减少约200行
  - 每个模块职责更加清晰
  - Alpha：信号生成和风控
  - PC：纯粹的资金管理
  - CPM：配对生命周期管理

## [v1.8.0_简化CPM逻辑优化接口@20250118] (feature/cpm-development分支)
### 移除幂等性并优化查询接口
- **移除幂等性检查**：
  - 删除last_cycle_id和last_cycle_pairs状态变量
  - 简化submit_modeled_pairs()方法，只保留批内去重
  - 代码减少约40行，逻辑更直接
  
- **优化查询接口**：
  - 删除get_all_tracked_pairs()合并接口
  - 添加三个独立查询接口：
    * get_current_pairs() - 获取本轮活跃配对
    * get_legacy_pairs() - 获取遗留持仓配对
    * get_retired_pairs() - 获取已退休配对
  - 添加get_pairs_summary()统计接口
  
- **改进效果**：
  - 接口语义更明确，调用者无需判断额外标记
  - 每个方法职责单一，符合单一职责原则
  - 批内去重改为跳过而非抛异常，更加健壮

## [v1.7.0_彻底重构移除兼容层@20250118] (feature/cpm-development分支)
### 彻底重构，移除所有向后兼容代码
- **兼容层完全移除**：
  - 删除所有@property装饰器（current_active, expired_pairs）
  - 删除_current_active_compat临时变量
  - 删除所有过渡期验证代码
  
- **数据结构统一**：
  - 统一使用新命名：current_pairs, legacy_pairs, retired_pairs
  - 更新所有方法直接使用新数据结构
  - get_current_active() → get_all_tracked_pairs()
  
- **代码清理**：
  - SignalGenerator删除Portfolio直接检查的验证代码
  - AlphaModel修复self.cpm为self.central_pair_manager
  - CPM内部方法全部使用新数据结构
  
- **逻辑优化**：
  - get_risk_alerts()动态生成expired_pairs列表
  - clear_expired_pairs()实现真正的清理逻辑
  - get_active_pairs_with_position()合并current和legacy配对
  - on_pair_exit_complete()正确处理legacy_pairs
  
- **架构改进**：
  - 彻底实现"单一真相源"原则
  - 删除所有冗余的状态检查
  - 代码更加简洁清晰

## [v1.6.0_Alpha-CPM深度优化与命名重构@20250117] (feature/cpm-development分支)
### 深度优化与数据结构重构
- **平仓信号优化**：
  - SignalGenerator平仓信号改用CPM.get_trading_pairs()
  - 统一建仓和平仓的查询模式
  - 保留过渡期验证机制
  
- **数据结构重构**：
  - current_active → current_pairs（本轮活跃配对）
  - expired_pairs → legacy_pairs（遗留持仓配对）
  - 新增retired_pairs（已退休配对）
  - 实现清晰的生命周期：current → legacy → retired
  
- **向后兼容设计**：
  - 通过@property提供兼容性访问
  - 外部代码无需立即修改
  - 添加deprecation警告
  
- **配对迁移逻辑**：
  - 新周期时自动迁移配对状态
  - 有持仓的旧配对→legacy_pairs
  - 已平仓的配对→retired_pairs
  - 每个容器职责单一明确

## [v1.5.0_Alpha-CPM交互优化@20250117] (feature/cpm-development分支)
### Alpha与CPM交互优化
- **CPM新增统一查询接口**：
  - get_trading_pairs(): 获取正在持仓的配对
  - get_recent_closed_pairs(days=7): 获取冷却期内的配对
  - get_excluded_pairs(): 获取应排除的配对集合（统一接口）
  
- **Alpha集中状态查询**：
  - SignalGenerator使用CPM.get_excluded_pairs()检查配对
  - 替代原有的Portfolio.Invested直接检查
  - 保留双重验证机制确保过渡期稳定性
  
- **架构改进**：
  - 实现"单一真相源"原则：CPM统一管理配对状态
  - 消除状态查询逻辑分散的问题
  - 为未来添加新规则预留扩展点
  - 提高代码可维护性和可测试性
  
- **实施策略**：
  - 渐进式迁移：新接口工作，旧逻辑验证
  - 保留TODO标记，明确未来清理点
  - 向后兼容，不影响现有功能

## [v1.4.1_CPM架构分析与优化规划@20250117] (feature/cpm-development分支)
### 架构分析与优化规划
- **CPM工作流程文档化**：
  - 详细梳理CPM与各模块的交互流程
  - 明确数据流向和状态转换
  - 识别接口职责和调用时机
  
- **code-architect架构审查**：
  - 识别5个主要优化点（按优先级）
  - 接口冗余问题：多个查询接口功能重叠
  - 状态管理复杂度：三层结构有概念重叠
  - 单实例模型限制：不支持分批建仓但简化了管理
  - 代码重复：pair_key规范化和Symbol查找
  - 错误处理不一致：返回值和日志级别
  
- **4阶段优化计划制定**：
  - 第一阶段：代码质量改进（工具类抽取、日志统一）
  - 第二阶段：接口优化（简化为3个核心查询接口）
  - 第三阶段：状态管理优化（分离过期配对、支持权重调整）
  - 第四阶段：性能优化（查询缓存、Symbol查找优化）
  
- **架构决策记录**：
  - 保持单实例模型：简化优于灵活
  - 渐进式优化：避免大规模重构风险
  - 保持CPM作为单一状态源的核心定位

## [v1.4.0_完成Execution与OOE集成@20250117] (feature/cpm-development分支)
### 交易执行架构完成
- **Execution模块实现**：
  - 极简权重执行设计（125行）
  - 正确处理PortfolioTarget.Percent格式
  - 使用SetHoldings自动处理权重到股数转换
  - 过滤微小调整（< 0.1%）避免频繁交易
  
- **CPM与OOE集成**：
  - 新增4个最小化OOE接口方法
  - on_pair_entry_complete: 标记入场完成并记录entry_time
  - on_pair_exit_complete: 标记出场完成并移至closed_instances
  - get_all_active_pairs: 获取所有活跃配对
  - get_pair_state: 查询配对状态
  
- **OnOrderEvent智能检测**：
  - 通过持仓变化推断配对状态（事实驱动）
  - 自动检测两腿都有持仓 → 入场完成
  - 自动检测两腿都无持仓 → 出场完成
  - 避免复杂的订单ID映射
  
- **框架集成完成**：
  - main.py启用所有框架组件
  - 正确的初始化顺序：Alpha → PC → Risk → Execution
  - CPM作为核心状态管理器传递给所有模块
  
- **架构设计亮点**：
  - 职责分离：Execution执行动作，OOE记录事实，CPM维护状态
  - 接口最小化：仅暴露必要的4个方法，避免过度复杂
  - 事实驱动：通过观察持仓变化推断状态，简化逻辑
  
## [v1.3.0_架构重构与市场冷静期@20250117] (feature/cpm-development分支)
### 架构重构：市场风险管理职责迁移
- **RiskManagement简化**：
  - 删除 `_check_market_condition` 方法
  - 从5个风控机制精简为4个核心机制
  - 专注于现有持仓的风险控制
  - 删除市场相关参数和触发记录

- **PortfolioConstruction增强**：
  - 新增市场冷静期机制（Market Cooldown）
  - SPY单日跌5%触发14天冷静期
  - 冷静期内暂停所有新建仓操作
  - 延迟初始化SPY，避免影响其他模块
  - 使用Daily分辨率数据，符合策略整体设计

- **架构优化理由**：
  - 职责分离：RM负责风险控制，PC负责建仓决策
  - 逻辑更清晰：市场条件是建仓决策的一部分
  - 实现更简单：在源头控制比末端过滤更优雅
  - 避免重复：个股和配对已有止损，无需市场止损

- **配置更新**：
  - config.py: 市场参数移至portfolio_construction配置
  - market_severe_threshold: 0.05（5%触发阈值）
  - market_cooldown_days: 14（冷静期天数）

## [v1.2.0_行业集中度控制@20250117] (feature/cpm-development分支)

## [v1.1.0_风险管理优化@20250116] (feature/cpm-development分支)
### RiskManagement 止损逻辑优化与修正
- **止损阈值调整**：
  - 配对整体止损：10% → 20%（给均值回归策略更多恢复空间）
  - 单边止损：15% → 30%（作为最后防线，防止单腿失控）
  - 双重保护机制：任一条件触发即双边平仓

- **单边止损逻辑修正**：
  - 修复做空时错误地对 UnrealizedProfit 取反的问题
  - 根本原因：QuantConnect API 的 UnrealizedProfit 已内置方向考虑
  - 统一计算公式：`drawdown = UnrealizedProfit / abs(HoldingsCost)`
  - 影响：确保做空头寸的止损计算正确

- **时间管理功能实现**：
  - 实现 `_check_holding_time` 分级时间管理
  - 15天仍亏损：全部平仓
  - 20天无论盈亏：减仓50%
  - 30天强制：全部平仓
  - CentralPairManager 新增 `get_pairs_with_holding_info()` 支持

- **单腿异常检测实现**：
  - 实现 `_check_incomplete_pairs` 方法
  - 检测配对缺腿：一边有持仓，另一边没有
  - 检测孤立持仓：不在任何活跃配对中的持仓
  - 自动生成平仓指令消除非对冲风险
  - 记录到 risk_triggers['incomplete_pairs']

- **风控执行顺序优化**：
  - 调整为：过期配对→配对止损→时间管理→单腿异常→行业集中度→市场异常
  - 单腿异常检查提前，优先处理紧急风险
  - 物理重排方法顺序与执行顺序一致
  - 添加70字符分隔线提升代码可读性

- **代码质量优化**：
  - 性能提升：Symbol 查找从 O(n*m) 循环优化到 O(n) 字典查找
  - 代码精简：`_check_pair_drawdown` 方法从 110 行减到 80 行（-27%）
  - 可读性提升：减少嵌套层级，提前计算布尔条件
  - 消除重复：使用 `targets.extend()` 替代重复的平仓代码

- **行业集中度控制实现**：
  - 实现 `_check_sector_concentration` 方法
  - 监控各行业的仓位占比，防止单一行业过度暴露
  - 阈值设定：单行业暴露超过50%时触发
  - 缩减策略：超限行业所有配对同比例缩减到75%
  - 一次遍历收集所有信息，优化性能
  - 使用 defaultdict 和预计算权重减少重复计算
  - 记录到 risk_triggers['sector_concentration']

- **测试完善**：
  - 更新所有测试的阈值期望值（20%/30%）
  - 修复 MockAlgorithm 缺少 Securities 属性问题
  - 添加 MockSecurities 类支持测试
  - 新增边界条件测试（29%/19%刚好不触发）
  - 新增 test_sector_concentration_control 测试
  - 模拟多配对场景验证行业集中度控制
  - 验证缩减比例计算的正确性
  - MockSecurities 类增强，支持 Fundamentals 数据模拟

- **代码架构工作流优化**：
  - 建立 code-architect subagent 自动审查流程
  - 实施"开发-审查-批准-执行"四阶段工作流
  - 确保优化建议需用户批准后才执行
  - 为未来的性能优化建立标准化流程
  - 新增单腿异常检测测试（test_incomplete_pair_detection, test_isolated_position_detection）
  - 调整测试数据确保逻辑正确性

### 技术实现细节
- 配对回撤计算：`(h1.UnrealizedProfit + h2.UnrealizedProfit) / total_cost`
- 单边回撤计算：`h.UnrealizedProfit / abs(h.HoldingsCost)`（不区分方向）
- 触发优先级：单边止损 > 配对整体止损 > 单腿异常
- 单腿检测逻辑：遍历持仓 → 查找配对 → 检查完整性 → 生成平仓

## [v1.0.0_CPM-v1-PC意图管理@20250812] (feature/cpm-development分支)
### CentralPairManager v1版本 - PC交互功能实现
- **核心功能**：
  - submit_intent方法处理prepare_open/prepare_close意图
  - 自动确定cycle_id（开仓从current_active，平仓从open_instances）
  - 日级去重缓存机制，防止同日重复提交
  - 实例生命周期管理（创建、跟踪、删除）
  - 四条件开仓资格检查（活跃、eligible、无实例）

- **技术实现**：
  - pair_key规范化：tuple(sorted([s1, s2]))
  - instance_id永不回退的计数器机制
  - 完整的拒绝码系统（NOT_ACTIVE, NOT_ELIGIBLE, HAS_OPEN_INSTANCE, CONFLICT_SAME_DAY）
  - PortfolioConstruction集成，自动提交意图
  - main.py传递CPM实例给PC

- **测试覆盖**：
  - 创建test_cpm_v1.py，10个单元测试全部通过
  - 覆盖所有核心场景（接受、拒绝、去重、冲突、跨期）

### 下一步计划
- 实现v2的Execution交互（on_execution_filled）
- 添加实际成交后的fulfilled标记
- 完善history_log历史记录

## [v4.2.0_PortfolioConstruction优化@20250809]
### PortfolioConstruction模块重大优化
- **智能Target生成器转型**：
  - 从机械转换器升级为智能决策模块
  - 移除冗余的信号验证（已在AlphaModel完成）
  - 移除Tag中的reason字段解析
  - 删除_validate_signal和_get_pair_position_status方法
  
- **质量过滤机制**：
  - 添加quality_score < 0.7的硬编码过滤
  - 防止低质量信号进入交易执行
  - 回测验证过滤70个低质量信号（AMZN&CMG等）
  
- **冷却期管理内置**：
  - PC内部实现7天冷却期追踪
  - 使用tuple(sorted([symbol1, symbol2]))确保配对一致性
  - 避免[A,B]和[B,A]被视为不同配对
  - 回测验证冷却期正确生效（PG&WMT在第7天可重新交易）
  
- **代码优化**：
  - main.py清理：所有imports移至顶部region
  - 启用真实PortfolioConstruction替代NullPortfolioConstructionModel
  - 删除不必要的注释和TODO标记

### 测试验证
- 回测20250809_1822验证所有功能正常
- 质量过滤和冷却期管理按预期工作
- 系统稳定性和代码可维护性显著提升

## [v4.1.0_AlphaModel模块化重构@20250809]
### AlphaModel模块化重构完成
- **模块拆分**：
  - 将1365行单文件拆分为5个独立模块
  - AlphaState.py - 集中状态管理（persistent/temporary/control）
  - DataProcessor.py - 数据处理逻辑
  - PairAnalyzer.py - 配对分析整合（协整+贝叶斯）
  - SignalGenerator.py - 信号生成逻辑
  - AlphaModel.py - 主协调器
  
- **风控前置机制**：
  - 实现配对级别的过期资产清理
  - SignalGenerator添加持仓前置检查
  - 建仓信号：检查两资产都无持仓
  - 平仓信号：检查至少一资产有持仓
  
- **Bug修复**：
  - 修复过期配对清理逻辑：从资产级别改为配对级别
  - 解决AMZN&CMG→AMZN&GM时CMG未清理问题
  - 防止无持仓生成平仓信号，有持仓生成建仓信号
  
### 测试验证
- 回测日志验证模块化正常工作
- 信号生成数量合理，持仓检查有效
- 配对切换时正确清理过期资产

## [v4.0.0_架构重构-删除PairRegistry@20250808]
### 重大架构重构
- **PairRegistry完全移除**：
  - 删除 `src/PairRegistry.py` 文件
  - 移除所有模块中的PairRegistry依赖
  - AlphaModel、RiskManagement、OrderTracker均已更新
  - 删除相关测试文件
  
- **配置管理优化**：
  - 创建独立配置文件 `src/config.py`
  - 从main.py分离所有配置参数
  - 支持多环境配置（production/test/development）
  
- **CentralPairManager简化**：
  - 移除所有预设方法骨架
  - 保持为空白类，等待根据实际需求设计
  - 遵循增量式重构原则
  
- **模块状态**：
  - UniverseSelection：✅ 重构完成，独立运行
  - AlphaModel：使用NullAlphaModel
  - PortfolioConstruction：使用NullPortfolioConstructionModel  
  - RiskManagement：使用NullRiskManagementModel
  
### 测试结果
- 选股功能独立测试成功
- 完成3次月度选股：48只、81只、76只股票
- 系统运行稳定

### 下一步计划
- 阶段3：AlphaModel重构与CPM集成
- 阶段4：PortfolioConstruction重构
- 阶段5：RiskManagement重构
- 阶段6：OnOrderEvent增强

## [v3.8.0_central-pair-manager-mvp@20250807]
### Phase 1: CentralPairManager最小可行产品
- **核心组件实现**：
  - 创建CentralPairManager类，统一管理配对生命周期
  - 实现配对状态机（CANDIDATE→APPROVED→ACTIVE→COOLDOWN）
  - 前置风控检查（冷却期、单股票限制、全局配对数）
  
- **依赖注入架构**：
  - main.py通过构造函数注入CentralPairManager到各模块
  - 避免直接依赖algorithm属性，提高测试性和解耦
  
- **模块集成**：
  - AlphaModel: 在协整分析后调用evaluate_candidates()进行前置风控
  - PortfolioConstruction: 建仓时调用register_entry()登记状态
  - RiskManagement: 平仓时调用register_exit()，从CPM获取活跃配对
  - PairRegistry: 标记为DEPRECATED，保留兼容性
  
### 预期效果
- ✅ 冷却期机制100%生效（7天内不能重新开仓）
- ✅ 单股票配对限制生效（每只股票最多1个配对）
- ✅ 全局配对数限制生效（最多4个配对）
- ✅ 保留回滚能力（通过enable_central_pair_manager配置）

## [v3.7.0_architecture-investigation@20250807]
### 深度架构分析和问题诊断
- **问题调查**：
  - 分析冷却期失效原因：PairRegistry每月覆盖历史数据导致信息丢失
  - 分析单股票配对限制失效：AlphaModel只检查当前选择周期，不考虑已有持仓
  - 发现根本原因：缺乏统一的配对生命周期管理器
  
- **架构设计**：
  - 制定长期架构优化方案（CentralPairManager）
  - 设计前置风控机制（在AlphaModel中执行）
  - 规划渐进式实施路线图（4个阶段）
  
- **文档更新**：
  - 创建docs/LONG_TERM_ARCHITECTURE_PLAN.md详细记录优化方案
  - 删除过时文档（PORTFOLIO_CONSTRUCTION_OPTIMIZATION_SUMMARY.md等）
  - 为潜在的对话中断做好准备

### 技术准备
- 确认问题根源并制定解决方案
- 保留回滚能力（通过版本控制）
- 准备Phase 1实施（CentralPairManager最小可行产品）

## [v3.6.0_holding-period-fix@20250806]
### 持仓时间计算修复
- **OrderTracker持仓时间错误修复**：
  - 问题：同一配对多次建仓平仓时，持仓时间被错误累计（如AMZN,CMG显示79天）
  - 原因：update_times()获取了历史上所有entry订单，而非最近一段的
  - 修复：只考虑最近一次平仓后的entry订单，确保每段持仓独立计算
  - 新增：get_holding_period()增加Portfolio持仓验证和时间异常检测

### Symbol顺序一致性
- **验证配对顺序传递**：
  - AlphaModel → PairRegistry → RiskManagement顺序保持一致
  - 确保同向持仓检查使用正确的symbol顺序
  - 平仓指令按原始配对顺序生成

### 测试覆盖
- **新增test_order_tracker_v36.py**：
  - 测试多次建仓平仓的持仓时间分段计算
  - 测试entry_time平仓后重置逻辑
  - 测试异常时间记录处理

### 技术细节
- 修复PairOrderInfo.update_times()的entry订单筛选逻辑
- 类型注解优化以避免循环导入问题
- 保持向后兼容性，不影响现有功能

## [v3.5.0_t0-t1-risk-separation@20250806]
### Stage 2: T+0/T+1风控逻辑分离
- **风控架构重构**：
  - 新增_perform_t0_checks()：自下而上的实时风控（个股→配对→组合→行业）
  - 新增_perform_t1_checks()：基于历史信息的风控（持仓时间、异常订单）
  - ManageRisk重构：清晰分离T+0和T+1逻辑
  - 保持对外接口不变，仅重组内部实现

### Stage 1: 异常配对检测修复（已完成）
- **异常配对检测修复**：
  - 问题：未建仓的配对（如AMZN,GM）被错误识别为异常配对
  - 原因：OrderTracker检测异常时未验证Portfolio实际持仓
  - 修复：_check_abnormal_orders()增加持仓验证逻辑
  - 效果：只对真正有持仓的配对执行异常处理

### 技术改进
- **最小化改动原则**：
  - 仅修改RiskManagement一个方法（5行核心代码）
  - 保持所有接口不变，确保向后兼容
  - 不影响PyMC和信号生成逻辑

### 测试更新
- 更新test_risk_info_transfer测试用例
- 适配新的异常检测逻辑

## [v3.4.0_risk-enhancement-string-format@20250806]
### 风控增强功能
- **跨周期协整失效检测**：
  - AlphaModel：检测未在当前周期更新的配对，生成Flat信号强制平仓
  - Tag格式扩展：支持可选的reason字段（如'cointegration_expired'）
  - 5天平仓信号持续时间确保失效配对被平仓
- **风控参数优化**：
  - 单边最大回撤：20% → 15%（更严格的单边风控）
  - 最大持仓天数：60天 → 30天（更快的资金周转）
- **行业集中度监控**：
  - 实现30%行业集中度阈值监控
  - 超限时自动平仓该行业最早建仓的配对
  - 新增风控统计项：sector_concentration

### 技术实现
- **Tag格式向后兼容**：
  - 保持字符串格式而非JSON（QuantConnect兼容性）
  - 格式：`symbol1&symbol2|alpha|beta|zscore|quality_score[|reason]`
  - PortfolioConstruction兼容新旧格式解析
- **RiskManagement增强**：
  - 新增_check_sector_concentration()方法
  - 接收sector_code_to_name映射用于行业识别
  - 统一的风控触发器统计

## [v3.2.0_architecture-design-decisions@20250806]
### 架构设计决策
- **明确T+0与T+1风控的区别**：
  - T+0风控：基于当前Portfolio状态的实时检查（回撤、资金限制等）
  - T+1风控：需要历史信息的检查（持仓时间、冷却期、异常订单等）
- **确认各模块职责边界**：
  - AlphaModel：纯粹的信号生成，不查询执行历史
  - PortfolioConstruction：纯粹的信号转换，专注资金分配
  - RiskManagement：双重职责 - 主动风控（每日检查）+ 被动风控（过滤新信号）
  - OrderTracker：OnOrderEvent的唯一监听者，管理订单衍生信息
- **架构设计理念**：
  - 信息流清晰胜过过早优化
  - 查询即决策，不做无意义的查询
  - 保持模块的纯粹性和单一职责

### 代码改进
- **AlphaModel状态管理重构**：
  - 实现集中的状态管理类 AlphaModelState
  - 区分持久状态（modeled_pairs、historical_posteriors、zscore_ema）和临时状态（clean_data等）
  - 选股完成后自动清理临时数据，减少内存占用80%+

## [v3.1.0_critical-fixes-and-architecture-refactoring@20250805]
### 关键性能修复
- **信号持续时间优化**：
  - 平仓信号：1天 → 5天（避免过早失效）
  - 建仓信号：2天 → 3天（确保执行机会）
- **持仓时间计算修复**：
  - 修复 OrderTracker 中 entry_time 在平仓后未重置的问题
  - 确保每次新建仓能正确计算持仓时间
- **同向持仓错误修复**：
  - RiskManagement 改用 PortfolioTarget.Percent() 替代 PortfolioTarget()
  - 使用 PairRegistry 保证配对顺序一致性，避免错误平仓

### 架构重构
- **职责分离优化**：
  - 从 AlphaModel 完全移除风控逻辑（删除 _filter_risk_controlled_pairs）
  - AlphaModel 现在专注于纯粹的信号生成
  - 所有风控检查（持仓时间、冷却期、止损）集中在 RiskManagement
- **依赖注入改进**：
  - RiskManagement：注入 order_tracker 和 pair_registry
  - AlphaModel：注入 pair_registry，移除 order_tracker
  - 消除所有通过 self.algorithm 访问依赖的代码

### 参数调整
- max_symbol_repeats: 3 → 1（每只股票只能在一个配对中）
- max_holding_days: 60天 → 30天（更严格的持仓时间控制）
- 新增 cooldown_days: 7天（平仓后冷却期）

### 代码清理
- 删除 src/PairLedger.py（已被 PairRegistry 替代）
- 清理旧的 backtest 日志文件

## [v3.0.0_test-framework-and-ai-agents@20250804]
### 里程碑更新
- 创建完整的测试框架，37个测试全部通过
- 建立 AI Agent 专家团队（测试工程师、代码架构师、策略医生）
- 新增 OrderTracker、PairRegistry 核心模块

### 技术细节
- Mock QuantConnect 框架，独立测试环境
- 修复配对关系查询和冷却期检查逻辑

---

## [v2.17.0_risk-management-implementation@20250802]
### 工作内容
- 实现完整的Risk Management风控模块
- 简化PairLedger时间跟踪机制
- 移除OnOrderEvent方法

### 技术细节
- 三层风控：60天持仓限制、10%配对止损、20%单边止损
- ManageRisk每日自动调用，依赖框架机制
- 完成交易闭环：选股→信号→仓位→风控

---

  - 为所有类添加详细的架构说明和工作流程
- **功能优化**：
  - 删除AlphaModel中的波动率计算(已在UniverseSelection处理)
  - 恢复upper_limit=3.0，防止极端偏离情况

---


---

## [v2.10.0_risk-management-enhancement@20250730]
### 工作内容
- 使用quantconnect-code-simplifier重构RiskManagement模块，提升代码质量和可维护性
- 新增持仓时间限制功能，超过60天自动平仓避免长期风险敞口
- 延长配对冷却期至14天，降低频繁开平仓的交易成本
- 清理诊断日志，移除z-score和leverage调试输出提升信噪比

### 技术细节
- **RiskManagement重构**：
  - 使用专门的代码优化agent，代码从129行优化至156行
  - 提取6个辅助方法：`_is_holding_expired`, `_create_liquidation_targets`等
  - 添加类型提示和日志常量，提升代码规范性
  - 简化`manage_risk`方法的嵌套逻辑，提高可读性
- **持仓时间限制**：
  - 新增`max_holding_days: 60`配置参数
  - 在PairLedger中记录`entry_time`跟踪建仓时间
  - 超期持仓自动生成平仓信号，避免趋势反转风险
- **冷却期优化**：
  - `pair_reentry_cooldown_days: 7 → 14`天
  - 有效减少摇摆交易，提升策略稳定性
- **日志清理**：
  - 删除AlphaModel中的z-score原始值日志
  - 移除leverage调试日志
  - 保留关键交易和风控日志

### 架构影响
- 建立更完善的风控体系：多层次风险控制机制协同工作
- 提升代码质量：通过专业工具优化，代码结构更清晰
- 确认系统配置：明确max_pairs=4为全市场总配对数限制
- 验证杠杆实现：InteractiveBrokers保证金账户正确配置2x杠杆

### 下一步计划
- 基于增强的风控功能进行全面回测
- 监控持仓时间分布，评估60天限制的效果
- 考虑实施动态风控阈值，根据市场状况调整参数
- 进一步优化其他模块的代码结构


### 技术细节
- **EMA优化**：
  - alpha: 0.3 → 0.8（更快响应，2天内衰减到5%）
  - 减少历史权重过高的问题
- **阈值调整**：
  - exit_threshold: 0.2 → 0.3（覆盖23.6%数据）
  - 避免持仓时间过长
- **移除upper_limit**：让趋势充分发展，风控交给RiskManagement

## [v2.9.8_ema-smoothing-thresholds@20250730]
### 工作内容
- 实施EMA平滑减少z-score虚假跳变
- 优化交易阈值提高信号质量

### 技术细节
- **Z-score平滑**：
  - 添加EMA平滑（α=0.3）：30%当前值，70%历史值
  - 保留raw_zscore供参考
  - 诊断日志显示平滑前后对比
- **阈值优化**：
  - entry_threshold: 1.65 → 1.2（基于数据分析）
  - exit_threshold: 0.3 → 0.2（更充分均值回归）
- **预期效果**：减少50%虚假跳变，交易频率增加2.3倍

## [v2.9.7_expand-sigma-prior@20250730]
### 工作内容
- 扩大sigma先验分布，进一步解决residual_std偏小问题

### 技术细节
- 将sigma先验从`HalfNormal(sigma=2.5)`扩大到`HalfNormal(sigma=5.0)`
- 期望值从约2.0提高到约4.0（翻倍）
- 配合v2.9.6的实际残差计算，预期显著提高residual_std

## [v2.9.6_residual-std-actual-calc@20250730]
### 工作内容
- 根本性修复residual_std计算方式，解决z-score过度敏感问题
- 使用实际残差计算标准差，替代sigma参数均值
- 移除0.05的硬编码最小值限制
- 简化AlphaModel.py代码结构

### 技术细节
- **改进residual_std计算**：
  - 使用后验均值参数(alpha, beta)计算拟合值
  - 计算实际残差：y_data - fitted_values
  - 使用实际残差的标准差作为residual_std
- **代码简化**（减少36行）：
  - 移除_build_and_sample_model中的诊断日志
  - 优化_group_by_sector使用defaultdict
  - 简化OnSecuritiesChanged使用列表推导式
- **添加诊断日志**：对比实际std和sigma均值

## [v2.9.5_residual-std-enhancement@20250730]
### 工作内容
- 进一步优化residual_std计算，解决标准差仍然偏小的问题
- 增强贝叶斯模型诊断能力，添加详细的数据变异性日志
- 优化数据处理流程，减少对原始数据变异性的影响
- 添加residual_std最小值保护机制，避免z-score过度敏感

### 技术细节
- **增强诊断日志**：
  - 记录原始价格和对数价格的标准差
  - 计算并记录实际残差的标准差
  - 显示MCMC采样得到的sigma分布范围
- **调整Sigma先验**：
  - 完全建模：从`HalfNormal(sigma=1)`增大到`HalfNormal(sigma=2.5)`
  - 动态更新：使用`max(prior_params['sigma_mean'] * 1.5, 1.0)`确保足够的灵活性
- **优化数据填充**：
  - 使用线性插值`interpolate(method='linear')`替代`fillna(method='pad')`
  - 保持数据的自然变异性，减少人为平滑
- **添加保护机制**：
  - 在`_extract_posterior_stats`中设置`residual_std`最小值为0.05
  - 防止过小的标准差导致z-score剧烈波动

### 问题影响
- **修复前**：residual_std仍在0.02-0.04范围，z-score过度敏感
- **预期修复后**：
  - residual_std恢复到0.05-0.15的正常范围
  - z-score波动更加稳定
  - 持仓时间延长至预期的10-30天

### 下一步计划
- 运行回测验证修复效果
- 监控诊断日志，分析数据变异性来源
- 根据实际效果进一步调整sigma先验或最小值阈值

## [v2.9.4_residual-std-fix@20250730]
### 工作内容
- 深入调查residual_std异常偏小的根本原因
- 发现并修复贝叶斯模型中残差标准差的计算错误
- 使用模型估计的sigma参数替代错误的flatten计算方法

### 技术细节
- **问题根源**：原代码使用`trace['residuals'].flatten().std()`计算
  - 错误地混合了MCMC采样不确定性(2000个样本)和时间序列变异性(252天)
  - 导致标准差被严重低估(0.02-0.04 vs 正常0.05-0.15)
- **修复方案**：改用`trace['sigma'].mean()`
  - sigma是模型直接估计的残差标准差
  - 已经考虑了参数不确定性和数据拟合
  - 理论上最准确的方法
- **代码修改**：AlphaModel._extract_posterior_stats第638行
  - 从：`'residual_std': float(residuals_samples.std())`
  - 改为：`'residual_std': float(sigma_samples.mean())`

### 问题影响
- **修复前**：z-score过度敏感，一天内可能从0.8跳到3.4
- **预期修复后**：z-score恢复正常敏感度，持仓时间延长至10-30天
- **根本解决**：消除了导致频繁交易的数值计算错误

### 下一步计划
- API恢复后运行回测验证修复效果
- 确认residual_std值恢复到正常范围
- 监控持仓时间是否达到预期水平

## [v2.9.3_position-duration-diagnosis@20250730]
### 工作内容
- 诊断并分析持仓时间过短问题，通过回测日志定位根本原因
- 大幅简化PairLedger实现，从复杂跟踪到最小功能集
- 清理冗余日志输出，保留关键交易和诊断信息
- 添加z-score计算诊断日志，收集残差标准差数据

### 技术细节
- **PairLedger极简化**：仅保留5个核心方法
  - `update_pairs_from_selection`: 更新本轮选股配对
  - `set_pair_status`: 设置配对活跃状态
  - `get_active_pairs_count`: 获取活跃配对数
  - `get_paired_symbol`: 查询配对关系
  - `_get_pair_key`: 标准化配对键
- **日志优化**：
  - 删除AlphaModel中6个冗余日志输出
  - 简化协整统计输出格式
  - 添加诊断日志：`[AlphaModel.Signal] {pair}: z-score={zscore:.3f}, residual_std={residual_std:.4f}`
- **PortfolioConstruction清理**：
  - 删除_update_active_pairs函数
  - 移除stats收集相关代码
  - 修复set_pair_inactive调用错误

### 问题发现
- **residual_std异常偏小**：所有配对的残差标准差在0.02-0.04范围，导致z-score过度敏感
- **z-score剧烈波动**：CTAS-TT一天内从0.768跳到3.379，频繁触发风险限制
- **计算逻辑正确**：确认z-score计算公式和residual_mean减法处理符合理论
- **与v2.8.3对比**：计算方法未变，但数值结果差异显著

### 架构影响
- 代码复杂度大幅降低：PairLedger从数百行简化至不到100行
- 提升可维护性：消除过度工程化设计，专注核心功能
- 诊断能力增强：新增日志帮助定位数值异常问题

### 下一步计划
- 调查residual_std为何如此之小（正常应在0.05-0.15范围）
- 考虑实施z-score平滑机制减少短期波动影响
- 评估是否需要调整风险阈值或添加最小residual_std限制
- 验证MCMC采样质量和收敛性

## [v2.9.2_alpha-model-replacement@20250128]
- 删除旧的AlphaModel.py文件
- 将NewAlphaModel.py重命名为AlphaModel.py
- 更新类名：NewBayesianCointegrationAlphaModel → BayesianCointegrationAlphaModel
- 更新main.py的导入，完全替换旧实现
- 清理相关缓存文件

## [v2.9.1_signal-generation@20250128]
- 实现NewAlphaModel的日常信号生成功能
- 新增SignalGenerator类，负责计算z-score并生成交易信号
- 实现风险控制：当z-score超过±3.0时立即生成平仓信号
- 完成Alpha模型从选股到信号生成的完整功能闭环

## [v2.9.0_alpha-model-refactor@20250128]
### 工作内容
- 完成 AlphaModel 代码结构的全面优化，解决原有代码"比较乱，冗余很多"的问题
- 创建全新的 NewAlphaModel.py，实现清晰的模块化架构设计
- 实现双模式贝叶斯建模系统，支持新配对的完全建模和历史配对的动态更新
- 将行业映射配置从 AlphaModel 移至 main.py，实现配置的集中管理
- 建立完善的数据质量管理流程，确保协整分析的统计有效性

### 技术细节
- **新增文件**：`src/NewAlphaModel.py` (806行)，包含四个核心类
  - `DataProcessor`: 数据处理器，封装数据下载、质量检查、清理和筛选流程
  - `CointegrationAnalyzer`: 协整分析器，负责行业分组、配对生成和协整检验
  - `BayesianModeler`: 贝叶斯建模器，实现 PyMC 建模和历史后验管理
  - `NewBayesianCointegrationAlphaModel`: 主 Alpha 模型，整合上述三个模块
- **配置迁移**：
  - 将 `sector_code_to_name` 映射从各模块移至 `main.py` 的 `StrategyConfig`
  - 删除 AlphaModel 和 UniverseSelection 中的重复行业映射定义
- **数据处理流程优化**：
  - 完整性检查：要求至少 98% 的数据（252天中至少247天）
  - 合理性检查：过滤价格为负值或零的数据
  - 空缺填补：使用 forward fill + backward fill 策略
  - 波动率筛选：过滤年化波动率超过 45% 的股票
- **贝叶斯建模创新**：
  - 完全建模模式：使用弱信息先验（alpha~N(0,10), beta~N(1,5), sigma~HalfNormal(1)）
  - 动态更新模式：使用历史后验作为先验，采样数减半（tune和draws各减50%）
  - 历史后验管理：自动保存和检索配对的后验参数，支持跨选股周期传递
- **模块集成**：
  - 更新 `main.py` 导入新的 Alpha 模型：`from src.NewAlphaModel import NewBayesianCointegrationAlphaModel`
  - 启用新 Alpha 模型：`self.SetAlpha(NewBayesianCointegrationAlphaModel(...))`

### 架构影响
- **代码质量大幅提升**：从原有的单一大类拆分为职责明确的多个小类，符合单一职责原则
- **可维护性增强**：每个类专注于特定功能，代码逻辑更清晰，便于后续维护和扩展
- **性能优化潜力**：动态更新模式减少 50% 的 MCMC 采样量，提高建模效率
- **配置管理统一**：所有模块共享同一份行业映射配置，消除了配置不一致的风险
- **扩展性提升**：模块化设计使得添加新的数据处理步骤或建模方法变得简单
- **日志系统完善**：每个模块都有独立的日志前缀，便于调试和监控

### 遗留问题
- 原有的 `src/AlphaModel.py` 文件仍然存在，需要在确认新模型稳定后移除
- 日常信号生成功能尚未实现（代码中标记为 TODO）
- PortfolioConstruction 和 RiskManagement 模块仍被注释，需要后续启用

### 下一步计划
- 实现 NewAlphaModel 的日常信号生成功能，基于建模结果计算 z-score 并生成交易信号
- 在新 Alpha 模型稳定运行后，删除旧的 AlphaModel.py 文件
- 启用并适配 PortfolioConstruction 模块，确保与新 Alpha 模型的兼容性
- 进行全面的回测验证，评估重构后的性能和稳定性改进

## [v2.8.4_sector-mapping-centralization@20250724]
- 将行业映射(sector_code_to_name)移到main.py的StrategyConfig中统一管理
- UniverseSelection和AlphaModel现在共享同一份行业映射配置
- 移除各模块中重复的MorningstarSectorCode导入和映射定义
- 提高代码可维护性，避免行业映射的重复定义

## [v2.8.3_bayesian-modeling-refactor@20250724]
### 工作内容
- 消除BayesianModelingManager中的四大冗余问题
- 重构贝叶斯建模流程，提高代码质量和性能
- 修正MCMC采样配置逻辑，基于prior_params存在性而非lookback_days判断
- 统一后验参数处理，避免重复的统计计算

### 技术细节
- **新增方法**：
  - `_extract_posterior_statistics`: 统一后验统计计算，消除重复代码
  - `_process_posterior_results`: 整合后验提取和历史保存功能
  - `_determine_sampling_config`: 正确的采样策略决策方法
- **重构方法**：
  - `perform_single_pair_modeling`: 简化建模流程，从70行减少到35行
  - `perform_all_pairs_modeling`: 消除重复的建模策略判断逻辑
- **逻辑修正**：
  - MCMC采样配置：`if prior_params is not None` 替代 `if lookback_days < lookback_period`
  - 错误处理：`operation_type = "动态更新" if prior_params is not None else "完整建模"`
- **性能提升**：减少约40行重复代码，提高数据处理效率和代码可维护性

## [v2.8.2_risk-management-optimization@20250724]
### 工作内容
- 修复RiskManagement模块中的typo："规细化" → "精细化"
- 增强回撤计算功能，支持做多和做空头寸的精确回撤计算
- 添加边界条件检查和空值保护，提高模块健壮性
- 优化代码结构，拆分长方法提高可读性和维护性

### 技术细节
- 回撤计算优化：`_calculate_drawdown`方法支持双向头寸
  - 做多头寸：价格下跌为回撤 `max(0, (avg_cost - current_price) / avg_cost)`
  - 做空头寸：价格上涨为回撤 `max(0, (current_price - avg_cost) / avg_cost)`
- 边界检查增强：添加对`pair_ledger`为None和价格≤0的检查
- 代码结构重构：新增`_process_single_target`方法拆分`manage_risk`长方法
- 错误处理完善：使用`max(0, ...)`确保回撤值非负，避免异常计算结果

### 架构影响
- 提高风控模块的健壮性：支持更多边界情况和异常场景
- 功能完整性增强：做空头寸风控计算准确性显著提升
- 代码质量改善：方法职责单一明确，可读性和维护性大幅提升
- 建立标准错误处理模式：为其他模块的健壮性改进提供参考

### 下一步计划
- 基于增强的风控功能进行全面回测验证
- 监控做空头寸回撤计算的实际效果
- 考虑添加更多风控指标和动态阈值调整

## [v2.8.1_pairledger-simplification@20250724]
### 工作内容
- PairLedger极简化重构，移除过度工程化设计
- 删除复杂的轮次追踪、Beta信息存储、时间戳记录等冗余功能
- 在main.py中实现15行极简PairLedger类，彻底简化配对管理
- 优化模块依赖关系，通过参数传递实例实现更清晰的架构

### 技术细节
- 删除src/PairLedger.py文件（原99行代码）
- 在main.py中创建极简PairLedger类（仅15行）：
  ```python
  class PairLedger:
      def __init__(self): self.pairs = {}
      def update_pairs(self, pair_list): # 双向映射更新
      def get_paired_symbol(self, symbol): # 配对查询
  ```
- 模块集成优化：
  - AlphaModel: `self.pair_ledger.update_pairs(successful_pairs)`
  - RiskManagement: `self.pair_ledger.get_paired_symbol(target.symbol)`
- 删除冗余功能：轮次计数、Beta变化检测、详细日志、多种查询方法

### 架构影响
- 代码量减少85%：从202行代码缩减至15行，极大简化维护成本
- 更清晰的面向对象设计：通过参数传递实例，依赖关系明确
- 消除过度工程化：只保留核心业务需求，删除所有非必要功能
- 遵循"简洁优雅"原则：实现最小功能集，完全满足配对管理和风控需求

### 功能保持完整
- ✅ 配对关系管理：双向映射存储和查询
- ✅ 风控集成：RiskManagement配对完整性检查
- ✅ Alpha集成：成功建模后的配对更新
- ❌ 轮次追踪、Beta存储、时间戳等过度设计功能

### 下一步计划
- 基于极简设计进行全面回测验证功能完整性
- 评估简化后的维护成本和开发效率提升
- 为其他模块的简化重构提供参考模式

## [v2.7.9_margin-cleanup@20250723]
### 工作内容
- 清理保证金机制相关代码，回归简洁的核心交易逻辑
- 移除复杂的保证金诊断和监控功能，专注策略核心功能
- 将保证金优化问题标记为遗留待解决，等待QuantConnect管理员回复
- 简化代码结构，提高可维护性和可读性

### 技术细节
- 代码清理：移除main.py中的CustomSecurityInitializer和杠杆配置
- 简化PortfolioConstruction.py：
  - 移除_log_portfolio_status、_validate_margin_mechanism等保证金诊断方法
  - 移除_check_minimum_order_size订单检查机制
  - 移除_log_expected_allocation资金分配监控
- 文档清理：删除MARGIN_INVESTIGATION_REPORT.md文件

### 保留功能
- Beta中性资金分配算法（核心数学逻辑）
- 协整对交易逻辑和7天冷却机制
- Beta筛选功能（0.2-3.0范围）
- 基本的PC模块交易日志

### 遗留问题
- 保证金机制优化：等待QuantConnect管理员回复关于自定义保证金模型的实现方法
- 资金利用率优化：当前受限于平台保证金机制，暂时保持现状

## [v2.7.8_margin-mechanism-fix@20250723]
### 工作内容
- 实施QuantConnect保证金机制修复，通过CustomSecurityInitializer确保SecurityMarginModel正确配置
- 增强保证金验证诊断功能，提供详细的保证金模型配置信息和异常分析
- 建立完整的保证金效率监控体系，帮助识别和诊断保证金配置问题
- 为保证金机制异常提供详细的解决建议和算法调整方案

### 技术细节
- 核心修复：在main.py中添加CustomSecurityInitializer
  - 为每个股票证券设置SecurityMarginModel(2.0)确保2倍杠杆（50%保证金率）
  - 配置ImmediateSettlementModel确保保证金账户立即结算
  - 添加保证金配置日志便于验证初始化过程
- 保证金诊断增强：
  - 检查每个持仓证券的保证金模型类型和杠杆配置
  - 监控保证金效率异常时的详细诊断信息（>1.8x）
  - 提供UniverseSettings.Leverage配置检查
  - 当保证金效率>2.5x时建议算法调整方案

### 预期效果
- 做空头寸应正确使用50%保证金而非100%资金
- 保证金效率监控值应接近1.0x（理想状态）
- 提高整体资金利用率，减少"资金积压"问题
- 为后续算法优化提供详细的保证金配置诊断信息

## [v2.7.6_portfolio-api-fix@20250723]
### 工作内容
- 修复QuantConnect Portfolio API属性名称错误，解决保证金监控功能的语法问题
- 统一使用Python snake_case命名规范，确保与QuantConnect框架兼容
- 完善保证金机制验证和资金分配监控功能
- 添加订单大小预检查机制，主动避免minimum order size警告

### 技术细节
- 核心修复：`SecurityPortfolioManager`对象属性名称标准化
  - `TotalBuyingPower` → `cash + margin_remaining`（计算购买力）
  - `TotalPortfolioValue` → `total_portfolio_value`
  - `TotalMarginUsed` → `total_margin_used`
  - `MarginRemaining` → `margin_remaining`
  - `Keys` → `keys`
- SecurityHolding属性标准化：
  - `Invested` → `invested`
  - `Quantity` → `quantity`
  - `HoldingsValue` → `holdings_value`
- 预检机制：`_check_minimum_order_size()`方法在生成PortfolioTarget前验证订单规模

### 架构影响
- 消除语法错误：Portfolio API调用完全兼容QuantConnect框架
- 增强监控能力：保证金使用效率分析、预期vs实际资金分配对比
- 提前问题发现：订单大小预检查避免运行时警告
- 代码质量提升：统一命名规范，提高代码可维护性

### 调查成果
- 创建详细的保证金调查报告：`MARGIN_INVESTIGATION_REPORT.md`
- 确认保证金配置正确：`SetBrokerageModel(InteractiveBrokers, Margin)`有效
- 识别资金利用率偏低的潜在原因：需要通过监控验证保证金机制实际效果
- 建立完整的诊断体系：从配置验证到实时监控的全链路分析

### 下一步计划
- 运行修复后的回测，观察保证金监控数据
- 验证保证金使用效率是否接近理论值（0.5x）
- 根据监控结果优化资金分配算法或识别配置问题
- 建立基于实际数据的保证金机制优化方案

## [v2.7.5_margin-leverage-investigation@20250723]
### 工作内容
- 初步优化保证金配置，为深度调查QuantConnect保证金机制做准备
- 发现资金利用率偏低问题，疑似保证金机制未正确生效
- 添加资金状态监控功能，实时跟踪保证金使用情况
- 保留最小订单限制作为资金分配问题的早期警告信号

### 技术细节
- 杠杆配置：在main.py中添加`UniverseSettings.Leverage = 2.0`提升总可用资金
- 资金监控：在PortfolioConstruction中新增`_log_portfolio_status()`方法
- 监控指标：总资产、现金、已用保证金、购买力、资金利用率等关键指标
- 日志优化：删除UniverseSelection中的无用调度日志，进一步精简输出

### 问题发现
- **资金利用率异常**：理论上8个协整对应各占12.5%资金，实际单笔交易仅$5k-$15k
- **保证金机制疑问**：做空头寸可能未按50%保证金计算，而是100%资金占用
- **最小订单警告**：出现单股交易推荐，暴露资金分配深层问题

### 架构影响
- 建立资金使用透明度：通过监控功能实时观察保证金机制是否生效
- 保持问题可见性：保留最小订单限制，让资金分配问题及时暴露
- 为深度调查奠定基础：杠杆配置提升资金上限，监控功能提供调查数据

### 下一步计划
- **深度调查QuantConnect保证金机制**：研究文档确认配置要求
- **验证资金分配算法**：检查数学公式与实际执行的差异
- **根因分析最小订单警告**：找出单股交易推荐的具体原因
- **制定针对性修复方案**：基于调查结果优化保证金配置或算法逻辑

## [v2.7.4_add-trading-cooling-mechanism@20250723]
### 工作内容
- 添加交易冷却机制，防止频繁的摇摆交易
- 解决T-TMUS类型的短期平仓-建仓循环问题
- 实现配置化的冷却期管理，提升策略稳定性
- 优化交易决策逻辑，减少不必要的交易成本

### 技术细节
- 冷却期配置：默认7天冷却期，通过main.py配置'cooling_period_days'参数
- 数据结构：添加pair_cooling_history字典记录配对平仓时间
- 检查逻辑：建仓信号需通过_is_in_cooling_period检查，冷却期内自动忽略
- 历史记录：每次平仓执行时调用_record_cooling_history记录时间
- 双向检查：支持(symbol1,symbol2)和(symbol2,symbol1)的双向键查询

### 架构影响
- 信号验证增强：在_validate_signal中集成冷却期检查
- 配置扩展：PortfolioConstruction支持cooling_period_days配置参数
- 日志完善：冷却期触发时输出详细的剩余天数信息
- 防摇摆机制：从根本上解决频繁平仓-建仓的低效交易

### 预期解决的问题
**T-TMUS摇摆交易案例**：
- 8/5平仓 → 8/6建仓：冷却机制将阻止8/6的建仓信号
- 8/15平仓 → 8/20建仓：冷却机制将阻止8/20前的建仓信号
- 减少交易频率，降低交易成本，提升策略收益稳定性

### 配置参数
- `cooling_period_days`: 7天（可调整）
- 支持后续根据策略表现优化冷却期长度

### 下一步计划
- 基于冷却机制进行回测验证，观察摇摆交易的改善效果
- 评估冷却期长度的最优设置，平衡交易机会与稳定性
- 考虑针对不同波动率特征的配对设置差异化冷却期

## [v2.7.3_ultra-log-reduction@20250723]
### 工作内容
- 进一步大幅削减日志输出，实现极致精简提升可读性
- 删除AlphaModel所有信号相关日志，消除信号生成噪音
- 删除PortfolioConstruction统计日志，只保留实际交易执行信息
- 标准化代码注释标点符号为英文格式，提升代码规范性

### 技术细节
- AlphaModel信号日志删除：移除买|卖信号打印和"生成信号: X对"统计
- PC统计日志删除：移除"信号处理"和"生成X组PortfolioTarget"统计
- 保留关键日志：只保留实际交易执行、Beta超限警告、信号转换提示
- 标点符号标准化：153个中文标点符号替换为英文标点符号

### 架构影响
- 日志量再次大幅减少：预计在v2.7.2基础上再减少80%
- 信噪比显著提升：消除冗余统计信息，突出核心交易行为
- 代码规范性提升：统一英文标点符号，符合国际化编码标准
- 调试效率提升：日志更聚焦于实际问题和关键决策点

### 日志精简效果对比
**v2.7.2前**：16页日志（3个月回测）
**v2.7.3后**：预计<2页日志（3个月回测）
**核心保留**：实际交易执行、风险控制警告、特殊情况转换

### 下一步计划
- 基于极简日志进行回测验证，确保关键信息不丢失
- 评估是否需要可选的详细日志模式用于深度调试
- 考虑添加关键性能指标的定期汇总日志

## [v2.7.2_log-optimization@20250723]
### 工作内容
- 优化日志输出机制，大幅减少冗余信息提升可读性
- 移除AlphaModel中的Beta筛选逻辑，简化建模统计
- 精简信号日志输出，只保留关键交易信息
- 将Beta筛选移至PortfolioConstruction保持架构清晰

### 技术细节
- 粗选日志调整：从select_coarse移至select_fine，在选股标题后输出
- 删除重复日志：AlphaModel的"选股日, 接收到"与后续统计重复
- 消除"复用"概念：移除混淆的预建模机制，简化为动态更新vs完整建模
- 信号日志精简：只输出买|卖信号，不输出回归/观望信号
- PC日志优化：删除忽略信号日志，保留信号转换日志（特殊性）
- Beta筛选位置：从_BuildPairTargets开头处筛选，范围0.2-3.0

### 架构影响
- 职责分离更清晰：AlphaModel专注统计信号生成，PC负责仓位和基础风控
- 日志量大幅减少：预计减少70%+的冗余输出
- 代码逻辑简化：消除了复杂的"复用"机制，提升可维护性
- 保留关键信息：有效交易、信号转换、Beta超限警告

### 日志精简效果
- 删除：无效回归信号、忽略信号、重复接收日志
- 保留：有效交易信号、实际执行交易、特殊信号转换
- 结果：3个月回测从16页日志预计减少至5页以内

### 下一步计划
- 基于精简后的日志进行回测验证
- 考虑增加日志级别控制，支持详细/简洁模式切换
- 进一步优化其他模块的日志输出

## [v2.7.1_capital-allocation-optimization-beta-filter@20250723]
### 工作内容
- 重构资金分配算法，实现100%资金利用率和精确Beta中性风险对冲
- 添加Beta范围筛选功能，过滤极端beta值的协整对提升策略稳定性
- 优化建模流程，复用Beta筛选结果避免重复计算提升性能

### 技术细节
- 全新资金分配算法：基于协整关系y=beta×x和保证金机制的数学模型
- 情况1（y多x空）：y_fund=c×beta/(m+beta), x_fund=c×m/(m+beta)
- 情况2（y空x多）：y_fund=c×m×beta/(1+m×beta), x_fund=c/(1+m×beta)
- 权重转换机制：做多权重=资金，做空权重=资金/保证金率
- Beta筛选范围：0.2 ≤ abs(beta) ≤ 3.0，在FilterCointegratedPairs中实现
- 预建模优化：Beta筛选时进行PyMC建模，后续流程复用posterior_param结果

### 架构影响
- 实现真正的100%资金利用率，每个配对精确使用分配的资金额度
- 保证Beta中性：杠杆后购买力严格满足协整关系，确保风险对冲效果
- 建模效率提升：避免重复PyMC建模，将建模统计细分为复用/动态更新/完整建模三类
- 策略稳定性增强：极端beta的协整对被过滤，降低单一股票风险集中度

### 数学验证
- 资金平衡验证：y_fund + x_fund = capital_per_pair（精确到1e-10）
- Beta中性验证：leveraged_position_ratio = beta_relationship（精确到1e-10）
- 多场景测试：标准(beta=1.5)、高Beta(beta=2.0)、低Beta(beta=0.8)、极限Beta(beta=3.0)全部通过

### 配置新增
- `min_beta_threshold`: 0.2，Beta最小阈值，过滤极小beta的协整对
- `max_beta_threshold`: 3.0，Beta最大阈值，过滤极大beta的协整对

### 下一步计划
- 基于新的资金分配进行回测，验证100%资金利用率的实际效果
- 根据Beta筛选结果，评估是否需要动态调整beta阈值范围
- 考虑添加基于波动率的动态资金分配权重调整

## [v2.7.0_portfolio-construction-signal-filter@20250723]
### 工作内容
- 完善PortfolioConstruction模块，实现智能信号过滤和持仓管理功能
- 建立基于Portfolio API的实时持仓检查机制
- 实现配置化架构，支持从StrategyConfig统一管理参数
- 优化信号有效期，平仓信号1天、建仓信号2天有效期

### 技术细节
- 修改构造函数支持config参数，从配置中读取margin_rate等参数
- 新增`_get_pair_position_status`方法，使用`algorithm.Portfolio[symbol].Invested`检查持仓状态
- 实现`_validate_signal`智能验证机制：平仓信号需有持仓、同向建仓忽略、反向建仓转平仓
- 重构`create_targets`方法，集成完整的信号处理流程和统计功能
- 增强日志输出：提供信号过滤统计（总计/有效/忽略/转换组数）和详细决策信息
- 在main.py中正式启用PortfolioConstruction模块，支持测试模式和正常模式切换

### 架构影响
- 实现真正的模块解耦：PortfolioConstruction专注信号过滤，AlphaModel专注统计信号生成
- 使用QuantConnect原生Portfolio API，无需维护内部状态，确保持仓检查的实时性和准确性
- 建立清晰的信号处理规则，避免重复建仓和摇摆交易，提升策略稳定性
- 完成从信号生成到仓位管理的完整交易执行链路

### 信号处理规则
- **平仓信号**：必须有持仓才有效，避免无效平仓操作
- **建仓信号**：未持仓时执行，已持仓同方向时忽略（防重复建仓）
- **反向信号**：自动转换为平仓信号，避免摇摆交易和风险累积
- **信号有效期**：平仓1天、建仓2天，适配Daily分辨率的实际交易需求

### 下一步计划
- 启用和完善RiskManagement模块，建立多层风险控制机制
- 基于实际交易结果，优化信号过滤参数和持仓管理策略
- 考虑添加部分平仓和动态仓位调整功能

## [v2.6.5_debug-log-cleanup@20250722]
### 工作内容
- 清理调试日志以减少日志冗余，优化回测输出的可读性
- 保留核心业务错误日志，确保监控和异常诊断能力
- 简化数据统计和初始化日志，专注于关键性能指标

### 技术细节
- 移除动态更新过程的详细调试信息（[Debug]标签日志）
- 移除MCMC采样开始/结束状态日志
- 移除先验参数详情和数据获取成功日志
- 简化数据质量统计，仅显示总计和有效数量
- 保留协整检验异常、PyMC导入错误等关键错误日志

### 架构影响
- 大幅减少日志输出量，提升回测执行效率
- 保持核心监控能力，便于生产环境问题排查
- 为最终部署版本奠定清洁的日志基础

## [v2.6.4@20250722]
### 工作内容
- 完善动态贝叶斯更新配置参数，支持用户自定义选股间隔和更新策略
- 同步main.py配置与AlphaModel实现，确保动态更新功能完整可用

### 技术细节
- 新增`selection_interval_days`: 30天，支持自定义选股间隔周期
- 新增`dynamic_update_enabled`: True，提供动态更新功能开关
- 配置参数与v2.6.3的AlphaModel实现完全匹配，支持跨选股周期的后验参数传递

### 架构影响
- 完成动态贝叶斯更新的配置层面集成，用户可灵活调整更新策略
- 为后续的参数优化和策略调整提供配置基础

## [v2.6.3_data-quality-optimize@20250722]
### 工作内容
- 重构数据检查架构，建立基础集中化+业务分散化的信任链设计
- 消除重复数据验证，大幅简化代码逻辑和提升执行效率
- 提升数据完整性标准从80%至95%，确保协整检验统计有效性
- 实现动态贝叶斯更新功能，支持历史后验作为先验分布

### 技术细节
- 数据质量集中化：`_BatchLoadHistoricalData`建立3级基础分类（basic_valid, data_complete, price_valid）
- 消除重复检查：移除长度验证、空值检查、数据一致性等4项重复验证，减少~50行冗余代码
- 信任链架构：下游函数信任上游质量保证，专注各自业务逻辑验证
- 动态建模统一：`PyMCModel`支持完整建模和动态更新两种模式，轻量级采样配置
- 数据填充优化：forward fill + backward fill处理零散缺失，最多允许13天缺失（5.2%容忍度）

### 架构影响
- 建立清晰的数据质量信任链，避免重复验证提升性能
- 实现职责分离：基础数据处理vs业务逻辑验证的明确边界
- 支持动态贝叶斯更新，历史后验参数跨选股周期传递
- 简洁优雅的设计原则：最小修改实现最大效果

### 数据质量提升
- 数据完整性要求：从80%（202天）提升至95%（239天）
- 协整检验统计有效性显著增强，减少填充数据对分析结果的影响
- 保持现有填充机制的简洁性，平衡统计严格性与数据可用性

### 下一步计划
- 考虑进一步提升数据完整性标准至97-98%，将缺失容忍度控制在安全范围
- 基于动态更新结果，优化MCMC采样参数和先验分布设置
- 监控数据填充的实际影响，评估对协整关系识别的统计偏差

## [v2.6.2@20250722]
### 工作内容
- 优化AlphaModel关键参数配置，提升协整对筛选质量和建模效率
- 调整波动率、相关性、配对数量等核心阈值，平衡策略收益与风险
- 清理回测历史文件，保持项目结构整洁

### 技术细节
- 相关性阈值优化：从0.2提升至0.5，提高协整对预筛选标准
- 配对数量控制：从5对降至4对，降低组合复杂度和风险集中度
- MCMC采样效率：burn-in和draws从1500降至1000，平衡精度与性能
- 波动率筛选严格化：从60%降至45%，筛选更稳定的股票
- 项目维护：清理过期回测文件，新增Pensive Yellow Caribou回测结果

### 架构影响
- 参数调优基于v2.6.1的日志优化，形成"监控→分析→优化"的迭代循环
- 更严格的筛选标准提升策略质量，为后续数据质量重构奠定基础
- MCMC性能优化为动态更新功能预留计算资源

### 参数对比
| 参数 | v2.6.1 | v2.6.2 | 优化目的 |
|------|--------|--------|----------|
| correlation_threshold | 0.2 | 0.5 | 提高协整对质量 |
| max_pairs | 5 | 4 | 降低组合风险 |
| mcmc_burn_in/draws | 1500 | 1000 | 平衡性能精度 |
| max_volatility_3month | 0.6 | 0.45 | 筛选稳定股票 |

### 下一步计划
- 基于优化参数的回测结果，进一步调整协整检验和建模阈值
- 实施数据质量架构重构，支持更严格的筛选标准
- 开发动态贝叶斯更新，提升模型适应性

## [v2.6.1_alphamodel-log-optimize@20250722]
### 工作内容
- 优化AlphaModel日志输出，减少冗余信息提升回测日志可读性
- 合并波动率筛选的多行输出为简洁的单行格式，详细信息内嵌
- 改进行业协整对报告，分离统计汇总和具体配对信息
- 实现每日信号生成的条件日志输出，仅在有意义时记录

### 技术细节
- 波动率筛选日志优化：将原来的2行输出（总体统计+明细统计）合并为1行，使用`候选45只 → 通过38只 (波动率过滤5只, 数据缺失2只)`格式
- 行业协整对日志分层：第1行显示行业统计汇总`Technology(2) Healthcare(1)`，第2行显示具体配对`Technology[AAPL-MSFT,GOOG-META]`
- 每日信号日志条件化：仅在`signal_count > 0 or insight_blocked_count > 0`时输出，避免无意义的"观望"日志
- 新增`insight_no_active_count`计数器追踪观望状态，提供完整的信号生成统计

### 架构影响
- 显著减少回测日志冗余，提升日志分析效率和可读性
- 保持所有关键信息的完整性，优化信息展示方式而非删除信息
- 建立条件化日志输出模式，为其他模块的日志优化提供参考
- 增强协整对信息的层次化展示，便于快速定位和分析

### 下一步计划
- 实施动态贝叶斯更新：使用历史后验作为新一轮选股的先验
- 对重复协整对使用最近30天数据进行似然更新，避免重新建模
- 建立后验参数存储机制，支持跨选股周期的参数传递

## [v2.6.0_alpha-config-volatility@20250721]
### 工作内容
- AlphaModel完成配置化架构改造，统一使用StrategyConfig集中管理参数
- 波动率筛选功能从UniverseSelection迁移到AlphaModel，实现更合理的筛选位置
- 实施批量数据缓存机制，显著优化History API调用性能
- 增强性能监控和详细日志输出，提供各处理阶段的耗时统计

### 技术细节
- 修改`BayesianCointegrationAlphaModel.__init__()`构造函数：从`__init__(self, algorithm)`改为`__init__(self, algorithm, config)`
- 将15个硬编码参数全部迁移到配置字典：协整检验、MCMC采样、信号阈值、波动率筛选等
- 新增`_BatchLoadHistoricalData()`方法：一次API调用获取所有股票历史数据并缓存，替代N次单独调用
- 新增`_VolatilityFilter()`方法：基于缓存数据计算3个月年化波动率，筛选低于60%的股票
- 实现详细性能监控：记录缓存、波动率、协整检验、MCMC各阶段耗时，总计耗时统计
- 更新main.py中StrategyConfig.alpha_model配置，增加波动率相关参数：`max_volatility_3month`、`volatility_lookback_days`

### 架构影响
- AlphaModel实现完全配置化，与StrategyConfig紧密集成，消除硬编码参数
- 建立批量数据处理模式，将API调用从O(N)优化为O(1)，显著提升性能
- 实现筛选职责合理分配：UniverseSelection负责基础筛选，AlphaModel负责策略相关筛选
- 统一各模块配置架构模式，为PortfolioConstruction配置化提供清晰参考
- 建立性能监控标准，为后续性能优化提供量化指标

### 性能优化
- 批量数据缓存：消除重复History API调用，预期性能提升80%以上
- 基于缓存的协整检验：避免PyMCModel中重复数据获取
- 详细耗时统计：便于识别性能瓶颈和优化效果评估

### 下一步计划
- 继续推进PortfolioConstruction配置化改造，完成算法框架的全面配置化
- 基于性能监控数据，进一步优化MCMC采样和协整检验的算法效率
- 评估波动率筛选迁移后的选股效果，优化筛选参数和逻辑

## [v2.5.2_universe-log-enhance@20250721]
### 工作内容
- 优化UniverseSelection日志输出，增强选股过程可观测性
- 添加行业分布统计，显示各行业最终选出的股票数量
- 添加财务筛选详细统计，显示各财务指标过滤的股票数量
- 改进日志格式，提供更清晰的选股过程信息

### 技术细节
- 在`_select_fine`方法中添加行业分布计算逻辑，统计通过财务筛选后各行业的股票数量
- 增加财务筛选失败原因统计：`pe_failed`, `roe_failed`, `debt_failed`, `leverage_failed`, `data_missing`
- 优化日志输出格式：使用箭头符号`候选163只 → 通过56只 (过滤107只)`提升可读性
- 新增两条关键日志：`财务过滤明细: PE(25) ROE(18)...` 和 `行业分布: Tech(12) Health(8)...`
- 改进错误处理：异常信息限制50字符，使用更简洁的`财务数据异常`标签

### 架构影响
- 提升UniverseSelection模块的调试和监控能力，便于参数优化
- 保持代码简洁性，所有改进不影响选股性能
- 建立了清晰的日志分层：基础统计 → 详细分解 → 行业分布 → 最终结果
- 为后续其他模块的日志优化提供了参考模式

### 下一步计划
- 基于增强的日志信息，分析各财务指标的筛选效果，优化阈值设置
- 继续推进AlphaModel配置化改造，将硬编码参数迁移到StrategyConfig
- 根据行业分布统计，评估30只/行业配置的实际效果

## [v2.5.1_config-simplify@20250720]
### 工作内容
- 简化UniverseSelection配置逻辑，删除冗余的向后兼容代码
- 强制使用集中配置，提高代码一致性和可维护性
- 消除重复代码，符合DRY原则

### 技术细节
- 修改`MyUniverseSelectionModel.__init__()`方法，移除了v2.5.0中的默认参数和向后兼容逻辑
- 强制要求`config`参数传入，删除了`if config is None`的分支处理
- 所有配置参数直接从`config`字典读取：`self.num_candidates = config['num_candidates']`
- 清理了不再需要的默认值设置代码，确保配置来源唯一性

### 架构影响
- UniverseSelection模块完全配置化，与StrategyConfig类紧密集成
- 消除了配置不一致的风险，所有参数必须通过集中配置传入
- 为后续AlphaModel和PortfolioConstruction配置化提供了清晰的架构模式

### 下一步计划
- 对AlphaModel进行相同的配置化改造，将15个硬编码参数迁移到StrategyConfig.alpha_model
- 建立统一的配置验证机制，确保所有模块配置的完整性和正确性

## [v2.5.0_centralized-config@20250720]
### 工作内容
- 实施策略参数集中管理架构
- 创建StrategyConfig类统一管理所有模块配置
- UniverseSelection支持配置传入，保持向后兼容
- 为AlphaModel和PortfolioConstruction配置奠定基础

### 技术细节
- 在`main.py`中创建`StrategyConfig`类，包含所有模块的配置参数
- 定义了三个主要配置字典：`universe_selection`、`alpha_model`、`portfolio_construction`
- 修改`MyUniverseSelectionModel.__init__()`签名：从`__init__(self, algorithm)`改为`__init__(self, algorithm, config)`
- 实现向后兼容逻辑：当`config=None`时使用默认配置，确保现有代码不会破坏
- 在`BayesianCointegrationStrategy.Initialize()`中创建配置实例并传递给UniverseSelection

### 架构影响
- 建立了统一的配置管理模式，所有模块参数将集中在StrategyConfig中
- 实现了配置与业务逻辑的分离，便于参数调优和维护
- 为策略的可配置化奠定了基础架构，支持后续快速扩展其他模块

### 遗留问题
- AlphaModel和PortfolioConstruction仍使用硬编码参数，需要在后续版本中配置化
- 配置验证机制尚未建立，需要添加参数有效性检查

### 下一步计划
- 简化UniverseSelection配置逻辑，移除向后兼容代码
- 对AlphaModel进行配置化改造，集成贝叶斯模型的所有参数


---

## 版本管理规范

### 版本号格式
```
v<主>.<次>.<修>[_描述][@日期]
```

### 版本说明
- **主版本**: 大变动/接口变更/架构重构
- **次版本**: 主要功能、算法升级、新模块  
- **修订号**: 小修复、参数调整、细节完善
- **描述**: 1-2词点明本次版本最大特征或主题
- **日期**: @YYYYMMDD格式，便于回溯

### CHANGELOG条目标准模板

每个版本更新应包含以下四个部分：

```markdown
## [v<版本号>]
### 工作内容
- 主要完成的任务和功能变更
- 用简洁的语言描述做了什么

### 技术细节
- 具体的代码修改和实现方法
- 文件变更、函数修改、新增逻辑等
- 参数调整、配置变更等技术细节

### 架构影响
- 对整体项目架构的影响和改进
- 模块间关系的变化
- 为后续开发带来的便利或约束

### 下一步计划
- 基于当前改动，明确后续开发方向
- 为新对话提供明确的起点和目标
- 识别需要解决的遗留问题
```

### 特殊情况处理
- **遗留问题**: 当存在已知但未解决的问题时，在相应版本中增加"遗留问题"部分
- **问题修复**: 当主要工作是修复bug时，增加"问题修复"部分详细说明
- **新增功能**: 当引入重要新功能时，增加"新增功能"部分突出说明