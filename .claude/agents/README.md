# Claude Agents for QuantConnect Development

This directory contains specialized AI agents to assist with QuantConnect strategy development.

## 📚 Agent 使用指南
**[查看完整的 Agent 团队使用指南](./AGENT_GUIDE.md)** - 详细了解各 Agent 的功能、应用场景和协作模式

## Available Agents

### 1. 🧪 QuantConnect 测试工程师 (quantconnect-test-engineer)
- **Purpose**: Create and manage comprehensive test suites for QuantConnect trading strategies
- **Capabilities**:
  - Unit testing for individual functions/methods
  - Integration testing for module interactions
  - Performance testing and benchmarking
  - Market scenario simulation
  - Test infrastructure creation
- **Usage**: Call this agent when you need to create, update, or debug tests for your strategy

### 2. 📐 代码架构师 (code-architect) 
- **Purpose**: Design and optimize code architecture for QuantConnect strategies
- **Capabilities**:
  - Architecture design and module integration
  - Code refactoring and optimization
  - Performance improvements
  - Design patterns implementation
  - Technical debt management
- **Usage**: Call this agent when you need to design new features, optimize existing code, or improve architecture

### 3. 🔍 策略医生 (strategy-doctor)
- **Purpose**: Diagnose problems and analyze strategy performance
- **Capabilities**:
  - Backtest log analysis
  - Problem diagnosis and root cause analysis
  - Performance bottleneck identification
  - Trading anomaly detection
  - Improvement recommendations
- **Usage**: Call this agent when you encounter issues, need to analyze backtest results, or diagnose unexpected behavior

## Quick Selection Guide

- **遇到问题？** → 找策略医生
- **想改进代码？** → 找代码架构师
- **需要测试？** → 找测试工程师
- **复杂任务？** → 查看 [AGENT_GUIDE.md](./AGENT_GUIDE.md) 了解协作模式

## How to Use

These agents are available in Claude and can be invoked when working on your QuantConnect projects. Each agent specializes in its domain and will provide targeted assistance based on your needs.