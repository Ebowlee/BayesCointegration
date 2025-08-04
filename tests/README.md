# BayesianCointegration 测试套件

本目录包含了 BayesianCointegration 策略的完整测试套件。

## 目录结构

```
tests/
├── mocks/                        # 模拟对象
│   └── mock_quantconnect.py     # QuantConnect 框架的模拟类
├── unit/                        # 单元测试
│   ├── test_order_tracker.py   # OrderTracker 模块测试
│   ├── test_pair_registry.py   # PairRegistry 模块测试
│   └── test_risk_management.py # RiskManagement 核心方法测试
└── integration/                 # 集成测试
    └── test_strategy_flow.py    # 策略流程集成测试
```

## 运行测试

### 使用测试运行脚本（推荐）

```bash
# 运行所有测试
python run_tests.py

# 只运行单元测试
python run_tests.py --unit

# 只运行集成测试
python run_tests.py --integration

# 运行特定模块的测试
python run_tests.py test_order_tracker      # 测试 OrderTracker
python run_tests.py test_pair_registry      # 测试 PairRegistry
python run_tests.py test_risk_management    # 测试 RiskManagement
python run_tests.py test_strategy_flow      # 测试集成流程

# 详细输出模式
python run_tests.py -v
python run_tests.py --unit test_risk_management -v
```

### 使用 unittest 直接运行

```bash
# 运行所有测试
python -m unittest discover tests

# 运行特定测试类
python -m unittest tests.unit.test_risk_management.TestRiskManagement

# 运行特定测试方法
python -m unittest tests.unit.test_risk_management.TestRiskManagement.test_single_stop_loss

# 直接运行单个测试文件
python tests/unit/test_order_tracker.py
```

## 测试覆盖范围

### OrderTracker 测试
- 订单创建和状态更新
- 配对识别
- 建仓/平仓检测
- 正常/异常配对判断
- 时间记录（建仓时间、平仓时间）
- 持仓天数计算
- 冷却期检查
- 待成交订单检查
- 旧记录清理

### PairRegistry 测试
- 配对列表更新
- 配对查询接口
- 股票包含检查
- 配对关系查询
- 边界情况处理

### RiskManagement 测试
- 回撤计算（配对回撤、单边回撤）
- 配对完整性检查
- 持仓超时平仓
- 配对止损触发
- 单边止损触发
- 冷却期过滤
- 异常订单检测
- 平仓指令生成

### 集成流程测试
- 配对完整生命周期
- 异常订单处理
- 止损触发流程
- 多配对管理
- 边界情况处理

## 注意事项

1. **独立运行**：测试使用模拟对象，不需要 LEAN 引擎或真实市场数据
2. **类型兼容**：由于 QuantConnect 的类型定义问题，某些测试可能需要调整
3. **路径问题**：确保从项目根目录运行测试
4. **Python 版本**：建议使用 Python 3.8+ 以获得最佳兼容性

## 扩展测试

如需添加新测试：
1. 在相应目录创建 `test_*.py` 文件
2. 继承 `unittest.TestCase`
3. 方法名以 `test_` 开头
4. 使用 `mocks/mock_quantconnect.py` 中的模拟类

## 持续集成

这些测试可以集成到 CI/CD 流程中：
- 在提交代码前运行所有测试
- 在合并请求时自动运行测试
- 定期运行测试确保代码质量