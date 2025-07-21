# 贝叶斯协整策略更新日志

版本格式：v<主>.<次>.<修>[_描述][@日期]

---

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