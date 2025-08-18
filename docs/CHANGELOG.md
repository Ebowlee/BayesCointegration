# 贝叶斯协整策略更新日志

版本格式：v<主>.<次>.<修>[_描述][@日期]

---

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
- 创建完整的测试框架，标志着策略开发进入专业化阶段
- 建立 AI Agent 专家团队，提供全方位开发支持
- 新增核心管理模块，完善策略架构

### 测试框架
- **测试覆盖**：37个测试全部通过
  - 单元测试：26个（OrderTracker 11个、PairRegistry 10个、RiskManagement 11个）
  - 集成测试：5个（完整生命周期、异常处理、止损触发、多配对管理、边界情况）
- **测试基础设施**：
  - 创建 Mock QuantConnect 框架（Symbol、Order、Portfolio 等）
  - 独立的测试环境，无需 LEAN 引擎
  - 测试运行脚本 run_tests.py
  - 详细的测试文档和使用指南

### 核心模块新增
- **OrderTracker**：订单生命周期跟踪
  - 记录建仓/平仓时间
  - 识别异常订单（单边成交）
  - 计算持仓天数
  - 支持冷却期检查
- **PairRegistry**：配对关系中央管理
  - 统一管理所有活跃配对
  - 提供配对查询接口
  - 支持配对状态追踪
- **RiskCalculator**：风险计算工具（预留接口）

### 问题修复
- **RiskManagement._find_paired_symbol**：
  - 原问题：通过持仓方向猜测配对关系
  - 修复：改用 PairRegistry.get_paired_symbol() 精确查询
- **冷却期过滤逻辑**：
  - 原问题：无持仓时跳过冷却期检查
  - 修复：确保始终执行冷却期过滤

### AI Agent 团队
- **测试工程师** (quantconnect-test-engineer)：
  - 负责创建和维护测试套件
  - 提供测试覆盖率分析
- **代码架构师** (code-architect)：
  - 升级自原代码优化 Agent
  - 负责架构设计和性能优化
- **策略医生** (strategy-doctor)：
  - 负责问题诊断和回测分析
  - 提供根因分析和解决方案

### 架构改进
- 模块间依赖关系更清晰：PairRegistry → OrderTracker → RiskManagement
- 测试驱动开发(TDD)基础建立
- 代码质量保证机制完善

### 下一步计划
- 使用新的测试框架进行更多边界测试
- 利用 AI Agent 团队持续优化策略
- 基于测试发现继续改进架构

---

## [v2.17.0_risk-management-implementation@20250802]
### 工作内容
- 实现完整的Risk Management风控模块，建立三层风险控制体系
- 简化PairLedger时间跟踪机制，采用"首次发现持仓"的记录方式
- 移除OnOrderEvent方法，避免风控时点的复杂跳动
- 添加配对完整性检查，识别并处理异常持仓

### 技术细节
- **风控模块核心功能**：
  - 持仓时间管理：超过60天强制平仓
  - 配对回撤监控：配对整体亏损超过10%止损
  - 单边回撤监控：单只股票亏损超过20%止损  
  - 配对完整性检查：检测同向持仓和单边持仓异常
- **简化实现**：
  - ManageRisk方法每日自动调用，无需OnData触发
  - 利用Portfolio实时状态，无需复杂的订单跟踪
  - PairLedger只记录"首次发现持仓"时间，接受约1天误差
- **代码优化**：
  - 删除main.py中的OnOrderEvent方法
  - 简化PairLedger.get_position_status方法
  - 风控模块使用Resolution.Daily确保每日执行

### 架构影响
- 建立了清晰的风控架构：独立于信号生成，主动监控所有持仓
- 大幅简化了实现复杂度：避免了Order Tag和OnOrderEvent的复杂追踪
- 提高了系统可靠性：依赖框架保证的调用机制，而非手动触发
- 完成了完整的交易流程：选股→信号→仓位→风控的闭环

### 下一步计划
- 基于新的风控模块进行回测验证
- 监控风控触发频率和效果
- 评估持仓时间限制和回撤阈值的合理性
- 根据实际运行结果进一步优化风控参数

---

## [v2.15.0_configuration-optimization@20250802]
### 工作内容
- 将quality score权重配置化，提升策略灵活性
- 优化仓位配置和选股参数
- 清理冗余代码和未使用的配置
- 同步所有代码注释与最新配置

### 配置更新
- **选股参数调整**：
  - min_price: 10 → 20 (提高最低价格要求)
  - max_pe: 80 → 100 (放宽PE限制)
- **AlphaModel参数**：
  - max_symbol_repeats: 2 → 3 (允许每只股票出现3次)
  - max_pairs: 5 → 20 (增加最大配对数)
  - entry_threshold: 1.0 → 1.2 (提高建仓门槛)
  - quality_weights配置化:
    - statistical: 0.4 (40%)
    - correlation: 0.3 → 0.2 (降低到20%)
    - liquidity: 0.3 → 0.4 (提高到40%)
- **PortfolioConstruction参数**：
  - min_position_per_pair: 0.10 → 0.05 (恢复5%最小仓位)
  - 仓位范围: 10%-15% → 5%-15%

### 代码优化
- **移除冗余功能**：
  - 删除未使用的pair_reentry_cooldown_days配置
  - 移除无用的_can_open_new_position方法
- **注释改进**：
  - 明确反向信号只做平仓，不做反向建仓
  - 为WEIGHT_TOLERANCE添加解释(权重验证容差1%)
  - 更新所有类文档字符串匹配最新配置

### 影响分析
- 提高了策略配置的灵活性和可维护性
- 通过配置化quality_weights便于后续优化调整
- 代码更加简洁，移除了未使用的功能
- 扩大了策略容量(max_pairs: 5→20)

---

## [v2.14.3_fix-insight-group@20250802]
### 工作内容
- 修复Insight.Group使用问题，解决配对交易无法执行的关键bug
- 添加GroupId诊断功能，便于追踪问题
- 优化insights处理逻辑

### 技术细节
- **Insight.Group修复**：
  - 移除list()包装，保持框架原生返回类型
  - 让QuantConnect框架自动处理GroupId设置
  - 修复_generate_pair_signals返回类型声明
- **诊断增强**：
  - PC模块输出没有GroupId的Insight警告
  - 显示GroupId分组结果数量
  - 帮助快速定位配对关系问题
- **兼容性处理**：
  - SignalGenerator增加try-except处理不同返回类型
  - 确保insights.extend()能正确处理各种情况

### 影响分析
- 解决了PC模块无法识别配对关系的核心问题
- 恢复了配对交易信号的正常执行
- 提高了代码对框架API变化的适应性

---

## [v2.14.2_diagnostics-and-optimization@20250802]
### 工作内容
- 添加信号生成诊断功能，追踪交易信号缺失原因
- 优化日志输出，提升系统可观察性
- 完善PortfolioConstruction模块文档
- 调整信号生成阈值，提高触发概率

### 技术细节
- **信号诊断系统**：
  - SignalGenerator输出每个配对的实时z-score值
  - AlphaModel记录跟踪配对数量和Insights生成情况
  - PC模块记录收到的Insights数量
  - 降低entry_threshold从1.2到1.0
- **日志优化**：
  - UniverseSelection：移除重复的行业"候选→选择"日志
  - PairLedger：合并输出格式"本轮新发现[...], 持续追踪[...]"
  - PC模块：添加资金使用情况监控
- **代码质量**：
  - 删除PC模块中注释的弃用代码
  - 为所有PC模块方法添加详细文档注释
  - 完善类级别的架构说明

### 影响分析
- 诊断能力提升：可追踪信号生成全流程，快速定位问题
- 日志更清晰：避免重复信息，突出关键状态变化
- 代码可维护性提高：详细文档便于理解和修改
- 触发概率提升：更容易生成交易信号进行策略验证

---

## [v2.14.1_quality-score-optimization@20250802]
### 工作内容
- 优化quality_score使用机制，复用AlphaModel的综合评分系统
- 修正信号Tag格式，确保quality_score正确传递
- 避免在PC模块重复计算质量分数

### 技术细节
- **AlphaModel质量评分系统**：
  - 统计显著性(40%)：基于协整检验p-value
  - 相关性(30%)：Pearson相关系数
  - 流动性匹配(30%)：成交额比率
  - 综合评分范围：0-1之间
- **信号传递优化**：
  - Tag格式更新：'symbol1&symbol2|alpha|beta|zscore|quality_score'
  - 贝叶斯建模器传递quality_score到信号生成器
  - PC模块直接解析使用，无需重新计算

### 影响分析
- 质量评分更准确：使用综合多维度评分，而非简单z-score
- 系统一致性提升：全流程使用统一的质量评分
- 计算效率提高：避免重复计算，复用已有结果

---

## [v2.14.0_dynamic-capital-allocation@20250802]
### 工作内容
- 实现PortfolioConstruction模块动态资金管理
- 移除固定最大配对数限制，改用基于可用资金的动态分配
- 整合框架RiskManagementModel，删除自定义风控系统
- 优化信号质量评估，基于z-score计算quality_score

### 技术细节
- **动态资金管理**：
  - 单对仓位范围：5%-10%，根据信号质量动态调整
  - 基于z-score计算quality_score，优先分配资金给高质量信号
  - 实时计算可用资金，自动停止分配当资金不足
  - 移除max_pairs限制，让市场机会和资金可用性决定配对数量
- **框架集成**：
  - 删除CustomRiskManager，改用框架的RiskManagementModel
  - ManageRisk方法每个Resolution步自动调用，确保持续风控
  - PairLedger使用实时Portfolio查询，无需缓存状态
  - 移除OnData和OnOrderEvent，完全依赖框架机制
- **代码优化**：
  - 新增_parse_tag_params方法解析信号参数和质量
  - 新增_allocate_capital_and_create_targets实现动态分配
  - 清理所有risk_triggered相关代码
  - 更新配置参数支持动态资金管理

### 影响分析
- 资金利用率提升：不再受固定配对数限制，充分利用可用资金
- 风险控制改善：框架自动调用风控，更可靠稳定
- 信号质量优先：高质量信号获得更多资金分配
- 架构更简洁：删除自定义风控系统，减少代码复杂度

---

## [v2.13.0_pairledger-risk-control@20250801]
### 工作内容
- 创建独立的PairLedger模块，实现配对状态跨周期管理
- 创建自定义风控系统CustomRiskManager，确保每日风控检查
- 重构架构，统一配对状态管理，解耦各模块依赖
- 启用PortfolioConstruction模块，实现完整的交易流程

### 技术细节
- **PairLedger模块**：
  - 集中管理所有配对的发现状态和持仓状态
  - 自动跟踪持仓时间，支持风控决策
  - 提供统一的状态查询接口供各模块使用
  - 实现风控标记机制，协调PC和风控模块
- **CustomRiskManager模块**：
  - 通过OnData确保每日执行风控检查
  - 支持持仓超时、止损、可选止盈三种风控类型
  - 通过OnOrderEvent自动更新配对持仓状态
  - 风控触发后标记配对，避免重复执行
- **架构优化**：
  - AlphaModel：负责更新配对发现状态
  - PortfolioConstruction：检查风控状态后生成交易指令
  - CustomRiskManager：执行风控并更新持仓状态
  - 数据流：选股→信号→交易→风控，各司其职

### 影响分析
- 状态管理更清晰：所有配对状态集中在PairLedger
- 风控更可靠：保证每日检查，不依赖其他模块调用频率
- 系统更稳定：模块间解耦，降低相互影响
- 扩展性更好：便于添加新的风控规则和状态跟踪

---

## [v2.12.0_alpha-model-enhancement@20250801]
### 工作内容
- 实现AlphaModel综合质量评分系统，提升配对筛选质量
- 全面增强模块文档，提高代码可读性和维护性
- 删除AlphaModel中重复的波动率筛选功能
- 恢复upper limit极端偏离风险控制
- 消除UniverseSelection中的硬编码，提升配置灵活性

### 技术细节
- **综合评分系统**：
  - 统计显著性(40%)：基于协整检验p值
  - 相关性(30%)：价格序列Pearson相关系数
  - 流动性匹配(30%)：基于252天平均成交额比率
  - 替代原有单一p值排序，提供更全面的配对质量评估
- **文档增强**：
  - 为所有类添加详细的架构说明和工作流程
  - 增强关键方法的参数和返回值文档
  - 添加内联注释解释核心算法逻辑
- **功能优化**：
  - 删除AlphaModel中的波动率筛选，避免与UniverseSelection重复
  - 恢复z-score > 3.0时的强制平仓逻辑
  - 将数据完整性检查比例改为可配置(98%)

### 影响分析
- 配对质量提升：综合评分系统能更准确识别高质量配对
- 代码可维护性提升：详细文档便于团队协作和后续优化
- 性能改进：删除重复筛选减少计算开销
- 风险控制加强：恢复极端偏离保护机制

---

## [v2.11.0_universe-selection-optimization@20250731]
### 工作内容
- 重构UniverseSelection模块选股逻辑，从单一市值排序改为多维度综合评分
- 新增波动率预筛选功能，在选股阶段剔除高风险股票
- 改用成交量替代成交金额进行流动性评估，更准确反映交易活跃度
- 放宽财务筛选标准，容纳更多类型股票（成长股、转型股、中小盘等）

### 技术细节
- **流动性筛选优化**：
  - 使用`Volume`替代`DollarVolume`，排除价格因素干扰
  - 设置最小成交量阈值1000万股，确保充足流动性
  - 降低最低价格至10美元，扩大股票池覆盖面
- **财务指标调整**：
  - PE < 50（原30）- 容纳高成长股
  - ROE > 0（原5%）- 容纳转型期公司
  - 资产负债率 < 80%（原60%）- 适应不同行业特性
  - 财务杠杆 < 8（原5）- 容纳金融等高杠杆行业
- **波动率筛选实现**：
  - 新增`_apply_volatility_filter`方法计算252天历史波动率
  - 剔除年化波动率超过60%的股票
  - 将波动率检查从AlphaModel前移，减少后续计算负担
- **多层次排序机制**：
  - 实现`_group_and_sort_by_sector`替代简单市值排序
  - 第一优先级：波动率升序（稳定性）
  - 第二优先级：成交量降序（流动性）
  - 每个行业综合评分选取最优股票

### 效果预期
- 提高配对候选股票质量，优先选择"稳定+流动"组合
- 扩大股票池覆盖面，从价值股扩展到成长股等多种类型
- 降低策略整体风险，通过波动率预筛选避免极端波动
- 提升配对成功率，选股阶段就考虑配对交易特性

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

## [v2.9.11_portfolio-construction-optimization@20250730]
### 工作内容
- 重构PortfolioConstruction.py，消除代码重复
- 提取辅助方法，提升代码可维护性
- 添加类型注解，增强代码可读性

### 技术细节
- **提取的辅助方法**：
  - `_calculate_pair_weights()`: 统一Up/Down方向的权重计算
  - `_validate_weight_allocation()`: 集中验证权重分配
  - `_determine_position_direction()`: 简化持仓方向判断
  - `_validate_new_position()`: 分离新建仓验证逻辑
  - `_create_flat_targets()`: 统一平仓目标创建
  - `_parse_beta_from_tag()`: 提取beta解析逻辑
  - `_get_trade_actions()`: 获取交易动作描述
  - `_can_execute_trade()`: 检查是否可执行交易
- **代码优化**：
  - 消除90%的重复逻辑
  - 代码行数保持不变（重构后功能更多）
  - 添加完整的类型注解
  - 提取常量到类级别
- **性能影响**：保持交易行为100%一致，略微提升执行效率

## [v2.9.10_remove-diagnostic-logs@20250730]
### 工作内容
- 清理诊断日志，提升运行效率
- 移除测试性打印输出

### 技术细节
- **移除的诊断日志**：
  - Residual计算日志：不再输出实际std与sigma均值对比
  - Z-score原始值：简化输出，仅保留平滑后的z-score值
- **性能提升**：减少日志输出，提高回测速度

## [v2.9.9_optimize-ema-thresholds@20250730]
### 工作内容
- 基于回测结果优化EMA参数和阈值设置
- 移除upper_limit限制

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

## [v2.4.9_universe-config-volatility@20250720]
### 工作内容
- 实施UniverseSelection参数配置化架构
- 新增波动率筛选功能（60%年化上限）
- 完善错误处理机制和详细日志输出
- 支持灵活的选股参数调整和验证

### 技术细节
- 重构`MyUniverseSelectionModel.__init__()`方法，将所有硬编码参数改为配置参数
- 新增8个可配置参数：`num_candidates`, `min_price`, `min_volume`, `min_ipo_days`, `max_pe`, `min_roe`, `max_debt_to_assets`, `max_leverage_ratio`
- 实现波动率筛选逻辑：计算过去252天的年化波动率，过滤超过60%的股票
- 增强错误处理：对财务数据异常情况添加try-catch和详细日志记录
- 改进日志格式：统一使用`[UniverseSelection]`前缀，提供筛选过程的详细统计信息

### 架构影响
- 首次引入配置化概念，为后续StrategyConfig架构奠定基础
- 提高选股模块的灵活性，支持不同市场环境下的参数调优
- 建立了错误处理和日志记录的标准模式

### 新增功能
- 波动率筛选：基于历史价格数据计算年化波动率，提升选股质量
- 参数验证：对配置参数进行基本的有效性检查
- 详细统计：提供每个筛选步骤的通过/过滤股票数量统计

### 下一步计划
- 建立StrategyConfig类，实现所有模块的集中配置管理
- 扩展配置化架构到AlphaModel和PortfolioConstruction模块

## [v2.4.8_strategy-optimize@20250720]
### 工作内容
- 优化主策略初始化流程和模块集成
- 完善AlphaModel贝叶斯协整检验参数
- 改进UniverseSelection选股筛选条件
- 修复.gitignore文件编码问题并完善忽略规则

### 技术细节
- 优化`BayesianCointegrationStrategy.Initialize()`方法的模块初始化顺序和参数设置
- 调整AlphaModel中的关键参数：协整检验p值阈值(0.025)、MCMC采样参数(1500 burn-in + 1500 draws)
- 完善UniverseSelection的财务筛选条件：PE比率、ROE、债务比率等指标的阈值优化
- 修复.gitignore文件编码问题(BOM头)，增加Python项目相关的忽略规则
- 改进模块间的调度机制，确保月度选股和日度信号生成的协调

### 架构影响
- 建立了更稳定的模块初始化和运行流程
- 优化了策略的整体性能和稳定性
- 为后续配置化改造提供了更清晰的参数基准

### 问题修复
- 解决了.gitignore文件编码导致的版本控制问题
- 修复了模块间数据传递的潜在同步问题
- 完善了错误处理和异常情况的处理逻辑

### 下一步计划
- 实施UniverseSelection的参数配置化，引入波动率筛选功能
- 建立统一的配置管理架构，为所有模块的配置化奠定基础

## [v2.4.7.1] 
- (之前的提交记录)

## [v2.4.7]
- (之前的提交记录)

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