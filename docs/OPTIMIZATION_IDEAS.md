# 策略优化想法

记录暂时无法实施但值得未来尝试的优化想法。

## 1. 基于半衰期的动态冷却期

**当前问题**：固定10天的冷却期对所有配对一视同仁

**优化方案**：
- 根据配对的历史半衰期动态设置冷却期
- 公式：`pair_cooldown_days = 2 * half_life`
- 半衰期短的配对（如5天）冷却期为10天
- 半衰期长的配对（如7天）冷却期为14天

## 2. 基于半衰期的动态持仓期限

**当前问题**：固定30天的最大持仓期不够灵活

**优化方案**：
- 根据配对的半衰期设置最大持仓期
- 公式：`max_holding_days = 4-5 * half_life`
- 给予慢速回归的配对更多时间
- 避免快速配对占用资金过久

## 3. 残差统计的计算方式对比

**背景**：在贝叶斯建模中提取后验统计量时，残差可以有两种计算方式

**方法1：直接从MCMC trace提取（当前实现）**
```python
residuals_samples = trace['residuals']  # MCMC采样的残差分布
residuals_mean = np.mean(residuals_samples)
residuals_std = np.std(residuals_samples)
```
- 优点：反映了参数不确定性下的残差分布
- 适用于：动态贝叶斯更新，保持分布信息的完整性

**方法2：使用后验均值重新计算**
```python
mu_fitted = alpha_mean + beta_mean * x_data
residuals_fitted = y_data - mu_fitted
residuals_mean = np.mean(residuals_fitted)
residuals_std = np.std(residuals_fitted)
```
- 优点：与实际交易时使用的参数一致
- 适用于：精确的点估计，Z-score信号生成

**结论**：当前采用方法1，因为在动态贝叶斯框架下，保留完整的分布信息更重要。未来可以考虑同时保存两种统计量。

---
更新时间：2025-01-29