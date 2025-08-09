# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict
import itertools
from statsmodels.tsa.stattools import coint
import pymc as pm
from .AlphaState import AlphaModelState
from .DataProcessor import DataProcessor
# endregion


# =============================================================================
# 协整分析模块 - CointegrationAnalyzer
# =============================================================================
class CointegrationAnalyzer:
    """
    协整分析器类 - 识别具有长期均衡关系的股票配对
    
    该类实现了配对交易的核心逻辑：寻找价格走势存在稳定关系的股票对。
    通过Engle-Granger协整检验和综合质量评分系统, 筛选出最优配对。
    
    核心功能:
    1. 行业内配对: 只在相同行业内寻找配对，确保业务相关性
    2. 协整检验: 使用Engle-Granger两步法检验长期均衡关系
    3. 综合评分: 结合统计显著性、相关性和流动性进行评分
    4. 智能筛选: 限制每只股票出现次数，确保分散化
    
    配对质量评分系统:
    - 统计显著性 (40%): 1 - pvalue, 协整关系的统计可靠性
    - 相关性 (20%): 价格序列的相关系数, 确保配对有意义
    - 流动性匹配 (40%): 成交额比率, 确保交易可执行性
    
    配置参数:
    - pvalue_threshold: 协整检验p值阈值(默认0.05)
    - correlation_threshold: 最低相关系数要求(默认0.7)
    - max_symbol_repeats: 每只股票最多出现次数(默认3)
    - max_pairs: 最多选择配对数量(默认20)
    
    工作流程:
    1. 按Morningstar行业分组
    2. 生成行业内所有可能的配对组合
    3. 执行协整检验和流动性计算
    4. 计算综合质量分数
    5. 按分数排序并筛选最优配对
    
    注意事项:
    - 协整关系可能随时间变化，需要定期更新
    - 行业内配对降低了系统性风险
    - 流动性匹配避免了执行风险
    """
    
    def __init__(self, algorithm, config: dict):
        """
        初始化协整分析器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含协整分析相关配置的字典
        """
        self.algorithm = algorithm
        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']
        self.max_symbol_repeats = config.get('max_symbol_repeats', 1)
        self.max_pairs = config.get('max_pairs', 5)
        
        # 配对质量评分权重配置
        # 权重设计理念：统计显著性是基础，相关性确保配对合理，流动性保证可执行
        self.quality_weights = config.get('quality_weights', {
            'statistical': 0.4,  # 统计显著性权重（1-pvalue）- 协整关系的统计可靠性
            'correlation': 0.3,  # 相关性权重 - 确保价格走势相似而非随机
            'liquidity': 0.3     # 流动性匹配权重 - 避免流动性差异导致的执行风险
        })
    
    def analyze(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame], 
                sector_code_to_name: Dict) -> Dict:
        """
        执行完整的协整分析流程
        
        Args:
            valid_symbols: 有效股票列表
            clean_data: 清洗后的数据
            sector_code_to_name: 行业代码到名称的映射
            
        Returns:
            包含协整对和统计信息的字典
        """
        # 初始化统计
        statistics = {
            'total_pairs_tested': 0,
            'cointegrated_pairs_found': 0,
            'sector_breakdown': {}
        }
        
        # 1. 行业分组
        sector_groups = self._group_by_sector(valid_symbols, sector_code_to_name)
        
        # 2. 生成配对并进行协整检验
        all_cointegrated_pairs = []
        
        # 对每个行业分别进行协整分析
        for sector_name, symbols in sector_groups.items():
            sector_pairs = self._analyze_sector(sector_name, symbols, clean_data)
            all_cointegrated_pairs.extend(sector_pairs)
            
            # 更新统计 - 直接计算组合数
            pairs_count = len(symbols) * (len(symbols) - 1) // 2
            statistics['sector_breakdown'][sector_name] = {
                'symbols': len(symbols),
                'pairs_tested': pairs_count,
                'pairs_found': len(sector_pairs)
            }
            statistics['total_pairs_tested'] += pairs_count
        
        # 3. 筛选配对（限制每个股票出现次数）
        filtered_pairs = self._filter_pairs(all_cointegrated_pairs)
        statistics['cointegrated_pairs_found'] = len(filtered_pairs)
        
        # 注意：配对结果现在直接返回，不再保存在实例中
        
        # 输出统计信息
        self._log_statistics(statistics, filtered_pairs)
        
        return {
            'cointegrated_pairs': filtered_pairs,
            'statistics': statistics
        }
    
    def _group_by_sector(self, valid_symbols: List[Symbol], sector_code_to_name: Dict) -> Dict[str, List[Symbol]]:
        """
        将股票按行业分组
        """
        result = defaultdict(list)
        for symbol in valid_symbols:
            security = self.algorithm.Securities[symbol]
            # 从基本面数据获取Morningstar行业分类
            sector_code = security.Fundamentals.AssetClassification.MorningstarSectorCode
            sector_name = sector_code_to_name.get(sector_code)
            
            if sector_name:
                result[sector_name].append(symbol)
        
        return dict(result)
    
    def _analyze_sector(self, sector_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        分析单个行业内的协整关系
        """
        cointegrated_pairs = []
        
        # 生成该行业内所有可能的股票配对组合
        # 使用itertools.combinations确保不重复且不自配对
        for symbol1, symbol2 in itertools.combinations(symbols, 2):
            prices1 = clean_data[symbol1]['close']
            prices2 = clean_data[symbol2]['close']
            
            # 测试配对协整性（传递完整数据用于流动性计算）
            pair_result = self._test_pair_cointegration(
                symbol1, symbol2, prices1, prices2, sector_name,
                clean_data[symbol1], clean_data[symbol2]  # 传递完整DataFrame用于流动性计算
            )
            if pair_result:
                cointegrated_pairs.append(pair_result)
        
        return cointegrated_pairs
    
    def _test_pair_cointegration(self, symbol1: Symbol, symbol2: Symbol, 
                                prices1: pd.Series, prices2: pd.Series, 
                                sector_name: str, data1: pd.DataFrame = None, 
                                data2: pd.DataFrame = None) -> Dict:
        """
        测试单个配对的协整性并计算流动性匹配
        
        Args:
            symbol1, symbol2: 股票代码
            prices1, prices2: 收盘价序列(用于协整检验)
            sector_name: 行业名称
            data1, data2: 完整的DataFrame(包含volume等, 用于流动性计算)
        """
        try:
            # Engle-Granger协整检验
            # 返回值：test statistic, p-value, critical values
            score, pvalue, _ = coint(prices1, prices2)
            
            # 计算Pearson相关系数，衡量价格序列的线性相关程度
            correlation = prices1.corr(prices2)
            
            # 检查是否满足条件
            # p值小于阈值表示拒绝"无协整关系"的原假设
            # 相关系数大于阈值确保配对有实际意义
            if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                # 计算流动性匹配分数
                liquidity_match = 0.5  # 默认值
                if data1 is not None and data2 is not None:
                    liquidity_match = self._calculate_liquidity_match(data1, data2)
                
                return {
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'pvalue': pvalue,
                    'correlation': correlation,
                    'sector': sector_name,
                    'liquidity_match': liquidity_match  # 新增字段
                }
        except Exception as e:
            pass  # 静默处理协整检验失败
        return None
    
    def _calculate_liquidity_match(self, data1: pd.DataFrame, data2: pd.DataFrame) -> float:
        """
        计算两只股票的流动性匹配度
        使用与协整检验相同的252天数据
        
        Args:
            data1, data2: 包含volume和close列的DataFrame
            
        Returns:
            float: 流动性匹配分数(0到1之间)
        """
        try:
            # 计算252天平均日成交额
            avg_dollar_volume1 = (data1['volume'] * data1['close']).mean()
            avg_dollar_volume2 = (data2['volume'] * data2['close']).mean()
            
            # 避免除零错误
            if avg_dollar_volume1 == 0 or avg_dollar_volume2 == 0:
                return 0
            
            # 成交额比率（0到1之间）
            # 比率越接近1，表示两只股票的流动性越匹配
            volume_ratio = min(avg_dollar_volume1, avg_dollar_volume2) / max(avg_dollar_volume1, avg_dollar_volume2)
            
            return float(volume_ratio)
            
        except Exception as e:
            pass  # 静默处理
            return 0.5  # 返回中性值
    
    def _calculate_pair_quality_score(self, pair: Dict) -> float:
        """
        计算配对的综合质量分数
        
        Args:
            pair: 包含pvalue, correlation, liquidity_match的配对信息字典
            
        Returns:
            float: 综合质量分数(0到1之间)
        """
        # 各项分数归一化到0-1
        statistical_score = 1 - pair['pvalue']  # pvalue越小，统计显著性越高
        correlation_score = pair['correlation']  # 已经在0-1之间
        liquidity_score = pair.get('liquidity_match', 0.5)  # 如果没有流动性数据，使用中性值
        
        # 加权综合
        quality_score = (
            self.quality_weights['statistical'] * statistical_score +
            self.quality_weights['correlation'] * correlation_score +
            self.quality_weights['liquidity'] * liquidity_score
        )
        
        return float(quality_score)
    
    def _filter_pairs(self, all_pairs: List[Dict]) -> List[Dict]:
        """
        使用综合评分系统筛选配对
        
        筛选原则:
        1. 按质量分数排序
        2. 每只股票只能出现在一个配对中（max_symbol_repeats=1）
        """
        
        # 为每个配对计算综合质量分数
        for pair in all_pairs:
            pair['quality_score'] = self._calculate_pair_quality_score(pair)
        
        # 按质量分数降序排序（替代原来的pvalue排序）
        sorted_pairs = sorted(all_pairs, key=lambda x: x['quality_score'], reverse=True)
        
        # 保持原有的筛选逻辑：限制每个股票的出现次数
        symbol_count = defaultdict(int)
        filtered_pairs = []
        
        for pair in sorted_pairs:
            # 达到最大配对数量限制
            if len(filtered_pairs) >= self.max_pairs:
                break
                
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']
            
            # 检查两只股票是否都未达到最大出现次数
            if (symbol_count[symbol1] < self.max_symbol_repeats and 
                symbol_count[symbol2] < self.max_symbol_repeats):
                filtered_pairs.append(pair)
                symbol_count[symbol1] += 1
                symbol_count[symbol2] += 1
        
        return filtered_pairs
    
    def _log_statistics(self, statistics: dict, filtered_pairs: List[Dict]):
        """输出统计信息"""
        # 输出选中配对的质量分数信息
        if filtered_pairs:
            scores = [p.get('quality_score', 0) for p in filtered_pairs if 'quality_score' in p]
            if scores:
                self.algorithm.Debug(
                    f"[AlphaModel.Coint] 合计筛选{len(filtered_pairs)}对: "
                    f"平均{np.mean(scores):.3f}, "
                    f"最高{max(scores):.3f}, "
                    f"最低{min(scores):.3f}"
                )
            
            # 按行业分组输出具体配对详情
            sector_pairs = defaultdict(list)
            for pair in filtered_pairs:
                sector = pair.get('sector', 'Unknown')
                symbol1 = pair['symbol1'].Value
                symbol2 = pair['symbol2'].Value
                quality_score = pair.get('quality_score', 0)
                sector_pairs[sector].append(f"({symbol1},{symbol2})/{quality_score:.3f}")
            
            # 输出每个行业的配对详情
            for sector, pairs in sorted(sector_pairs.items()):
                pairs_str = ", ".join(pairs)
                self.algorithm.Debug(f"[AlphaModel.Coint] {sector}: {pairs_str}")


# =============================================================================
# 贝叶斯建模模块 - BayesianModeler
# =============================================================================
class BayesianModeler:
    """
    贝叶斯建模器类 - 使用MCMC方法估计配对交易参数
    
    该类实现了贝叶斯统计框架下的配对关系建模, 通过MCMC采样获得
    参数的完整后验分布, 提供比传统OLS更丰富的不确定性量化。
    
    核心模型:
    log(price1) = alpha + beta * log(price2) + epsilon
    其中:
    - alpha: 截距项, 反映两只股票的基础价差
    - beta: 斜率项, 反映价格敏感度(对冲比率)
    - epsilon: 误差项, 假设服从正态分布N(0, sigma²)
    
    建模特性:
    1. 完全建模: 首次发现配对时，使用无信息先验
    2. 动态更新: 重复配对时，使用历史后验作为先验
    3. 不确定性量化: 提供参数的均值和标准差
    4. 自适应采样: 根据建模类型调整采样参数
    
    MCMC配置:
    - mcmc_warmup_samples: 预热采样数(默认1000)
    - mcmc_posterior_samples: 后验采样数(默认1000)
    - mcmc_chains: 马尔可夫链数量(默认2)
    - lookback_period: 历史数据有效期(默认252天)
    
    先验设置:
    - 完全建模: alpha~N(0,10), beta~N(1,5), sigma~HalfNormal(5)
    - 动态更新: 使用历史后验参数，采样数减半
    
    优势:
    - 参数不确定性的完整刻画
    - 自然的贝叶斯更新机制
    - 对异常值的鲁棒性
    - 小样本下的稳定性
    
    注意事项:
    - MCMC采样计算密集, 需要平衡精度和速度
    - 先验选择会影响结果, 特别是小样本情况
    - 动态更新避免了参数的剧烈跳动
    """
    
    def __init__(self, algorithm, config: dict, state: AlphaModelState):
        """
        初始化贝叶斯建模器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含MCMC相关配置的字典
            state: AlphaModel的状态管理对象
        """
        self.algorithm = algorithm
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        self.lookback_period = config['lookback_period']
        self.state = state
    
    def model_pairs(self, cointegrated_pairs: List[Dict], clean_data: Dict) -> Dict:
        """
        对所有协整对进行贝叶斯建模
        """
        modeled_pairs = []
        statistics = defaultdict(int, total_pairs=len(cointegrated_pairs))
        
        for pair in cointegrated_pairs:
            result = self._model_single_pair(pair, clean_data)
            if result:
                modeled_pairs.append(result)
                statistics['successful'] += 1
                statistics[f"{result['modeling_type']}_modeling"] += 1
            else:
                statistics['failed'] += 1
        
        self._log_statistics(dict(statistics))
        
        return {
            'modeled_pairs': modeled_pairs,
            'statistics': dict(statistics)
        }
    
    def _model_single_pair(self, pair: Dict, clean_data: Dict) -> Dict:
        """
        处理单个配对的完整流程
        
        Args:
            pair: 协整对信息
            clean_data: 清洗后的数据
            
        Returns:
            建模结果字典, 失败返回None
        """
        try:
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']
            
            # 提取价格数据
            prices1 = clean_data[symbol1]['close'].values
            prices2 = clean_data[symbol2]['close'].values
            
            # 获取历史后验（如果存在）
            # 实现动态贝叶斯更新：使用上次的后验作为本次的先验
            prior_params = self._get_prior_params(symbol1, symbol2)
            
            # 执行贝叶斯建模和采样
            trace, x_data, y_data = self._build_and_sample_model(prices1, prices2, prior_params)
            
            # 构建结果（使用展开操作符避免冗余）
            result = {
                **self._extract_posterior_stats(trace, x_data, y_data),
                'symbol1': symbol1,
                'symbol2': symbol2,
                'sector': pair['sector'],
                'quality_score': pair.get('quality_score', 0.5),  # 传递quality_score
                'modeling_type': 'dynamic' if prior_params else 'full',
                'modeling_time': self.algorithm.Time
            }
            
            # 保存后验用于未来的动态更新
            self._save_posterior(symbol1, symbol2, result)
            
            return result
            
        except Exception as e:
            pass  # 静默处理建模失败
            return None
    
    def _get_prior_params(self, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """
        获取历史后验参数
        """
        pair_key = (symbol1, symbol2)
        
        # 一次性检查存在性和有效性
        historical_posteriors = self.state.persistent['historical_posteriors']
        if pair_key not in historical_posteriors:
            return None
        
        historical = historical_posteriors[pair_key]
        time_diff = self.algorithm.Time - historical['update_time']
        
        if time_diff.days > self.lookback_period:
            pass  # 不需要输出过期信息
            return None
        
        return historical
    
    def _build_and_sample_model(self, prices1: np.ndarray, prices2: np.ndarray, 
                                prior_params: Dict = None) -> Tuple[pm.backends.base.MultiTrace, np.ndarray, np.ndarray]:
        """
        构建贝叶斯线性回归模型并执行MCMC采样
        
        核心模型: log(price1) = alpha + beta * log(price2) + epsilon
        
        该方法是贝叶斯建模的核心, 通过MCMC采样获得参数的完整后验分布。
        使用对数价格确保模型的线性假设更合理，并避免异方差问题。
        
        Args:
            prices1: 股票1的价格数据(原始价格)
            prices2: 股票2的价格数据(原始价格)
            prior_params: 历史后验参数字典, 包含:
                - alpha_mean, alpha_std: 截距项的先验
                - beta_mean, beta_std: 斜率项的先验
                - sigma_mean: 误差项标准差的先验
                如果为None, 使用无信息先验
            
        Returns:
            Tuple[MultiTrace, ndarray, ndarray]:
                - trace: MCMC采样结果, 包含所有参数的后验样本
                - x_data: 对数转换后的自变量(log(price2))
                - y_data: 对数转换后的因变量(log(price1))
        
        模型细节:
            - alpha: 截距项，反映两股票的基础价差
            - beta: 斜率项, 即对冲比率, beta=0.8表示1份股票1对冲0.8份股票2
            - sigma: 残差标准差, 反映模型拟合的不确定性
            - residuals: 模型残差, 用于计算z-score
        
        采样策略:
            - 完全建模: 1000次预热 + 1000次采样
            - 动态更新: 500次预热 + 500次采样(利用好的先验)
        """
        # 对数转换 - 确保线性假设合理性并稳定方差
        x_data = np.log(prices2)
        y_data = np.log(prices1)
        
        # 使用PyMC上下文管理器构建贝叶斯模型
        with pm.Model() as model:
            # 设置先验和采样参数
            priors = self._setup_priors(prior_params)
            alpha = priors['alpha']
            beta = priors['beta']
            sigma = priors['sigma']
            tune = priors['tune']
            draws = priors['draws']
            
            # 定义线性关系
            # mu = E[log(price1) | log(price2)] = alpha + beta * log(price2)
            mu = alpha + beta * x_data
            
            # 定义似然函数
            # 假设残差服从正态分布：log(price1) ~ Normal(mu, sigma)
            likelihood = pm.Normal('y', mu=mu, sigma=sigma, observed=y_data)
            
            # 计算残差（用于后续分析）
            # 残差 = 实际值 - 预测值，用于计算z-score
            residuals = pm.Deterministic('residuals', y_data - mu)
            
            # 执行MCMC采样
            # NUTS采样器会自动调整步长，高效探索后验分布
            trace = pm.sample(
                draws=draws,      # 后验采样数
                tune=tune,        # 预热采样数（用于调整采样器）
                chains=self.mcmc_chains,  # 并行马尔可夫链数量
                return_inferencedata=False,
                progressbar=False  # 避免在QuantConnect中显示进度条
            )
        
        
        return trace, x_data, y_data
    
    def _setup_priors(self, prior_params: Dict):
        """
        设置模型先验
        """
        if prior_params is None:
            # 无信息先验（首次建模）
            return {
                'alpha': pm.Normal('alpha', mu=0, sigma=10),      # 截距项：中心在0，较大不确定性
                'beta': pm.Normal('beta', mu=1, sigma=5),         # 斜率项：中心在1（等权重），中等不确定性
                'sigma': pm.HalfNormal('sigma', sigma=5.0),       # 误差项：半正态分布确保非负
                'tune': self.mcmc_warmup_samples,                 # 完整的预热采样
                'draws': self.mcmc_posterior_samples              # 完整的后验采样
            }
        else:
            # 信息先验（动态更新）- 使用历史后验作为先验
            return {
                'alpha': pm.Normal('alpha', mu=prior_params['alpha_mean'], sigma=prior_params['alpha_std']),
                'beta': pm.Normal('beta', mu=prior_params['beta_mean'], sigma=prior_params['beta_std']),
                'sigma': pm.HalfNormal('sigma', sigma=max(prior_params['sigma_mean'] * 1.5, 1.0)),  # 稍微放宽不确定性
                'tune': self.mcmc_warmup_samples // 2,            # 减少预热次数（因为有好的初始值）
                'draws': self.mcmc_posterior_samples // 2         # 减少采样次数（收敛更快）
            }
    
    def _extract_posterior_stats(self, trace, x_data, y_data) -> Dict:
        """
        从MCMC采样结果中提取后验统计量
        
        Args:
            trace: MCMC采样结果
            x_data: 对数转换后的自变量数据(用于计算实际残差)
            y_data: 对数转换后的因变量数据(用于计算实际残差)
        """
        # 提取所有需要的样本
        samples = {
            'alpha': trace['alpha'].flatten(),
            'beta': trace['beta'].flatten(),
            'sigma': trace['sigma'].flatten(),
            'residuals': trace['residuals'].flatten()
        }
        
        # 计算统计量
        stats = {f"{key}_mean": float(val.mean()) for key, val in samples.items() if key != 'residuals'}
        stats.update({f"{key}_std": float(val.std()) for key, val in samples.items() if key in ['alpha', 'beta', 'sigma']})
        stats['residual_mean'] = float(samples['residuals'].mean())
        
        # 使用后验均值参数计算实际残差的标准差
        # 这比使用MCMC采样的残差更稳定
        alpha_mean = stats['alpha_mean']
        beta_mean = stats['beta_mean']
        fitted_values = alpha_mean + beta_mean * x_data  # 预测值
        actual_residuals = y_data - fitted_values        # 实际残差
        
        # 使用实际计算的标准差（用于z-score计算）
        stats['residual_std'] = float(np.std(actual_residuals))
        
        return stats
    
    def _save_posterior(self, symbol1: Symbol, symbol2: Symbol, stats: Dict):
        """
        保存后验参数
        """
        pair_key = (symbol1, symbol2)
        
        # 只保存需要的统计量
        keys_to_save = ['alpha_mean', 'alpha_std', 'beta_mean', 'beta_std', 'sigma_mean', 'sigma_std']
        self.state.persistent['historical_posteriors'][pair_key] = {
            **{k: stats[k] for k in keys_to_save},
            'update_time': self.algorithm.Time
        }
    
    def _log_statistics(self, statistics: dict):
        """输出建模统计信息"""
        successful = statistics.get('successful', 0)
        failed = statistics.get('failed', 0)
        full = statistics.get('full_modeling', 0)
        dynamic = statistics.get('dynamic_modeling', 0)
        
        self.algorithm.Debug(
            f"[AlphaModel.Bayesian] 建模完成: 成功{successful}对, 失败{failed}对 "
            f"(完全建模{full}对, 动态更新{dynamic}对)"
        )


# =============================================================================
# 配对分析器包装类 - PairAnalyzer
# =============================================================================
class PairAnalyzer:
    """
    配对分析器 - 整合数据处理、协整分析和贝叶斯建模
    
    该类是月度配对发现的主入口，协调三个子模块完成完整的配对分析流程。
    """
    
    def __init__(self, algorithm, config: dict, sector_code_to_name: dict, state: AlphaModelState):
        """
        初始化配对分析器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 配置字典
            sector_code_to_name: 行业代码到名称的映射
            state: AlphaModel的状态管理对象
        """
        self.algorithm = algorithm
        self.config = config
        self.sector_code_to_name = sector_code_to_name
        self.state = state
        
        # 初始化子模块
        self.data_processor = DataProcessor(algorithm, config)
        self.cointegration_analyzer = CointegrationAnalyzer(algorithm, config)
        self.bayesian_modeler = BayesianModeler(algorithm, config, state)
    
    def analyze(self, symbols: List[Symbol]) -> Dict:
        """
        执行完整的配对分析流程
        
        Args:
            symbols: 待分析的股票列表
            
        Returns:
            包含分析结果的字典：
            - modeled_pairs: 建模后的配对列表
            - statistics: 分析统计信息
        """
        # 步骤1: 数据处理
        data_result = self.data_processor.process(symbols)
        clean_data = data_result['clean_data']
        valid_symbols = data_result['valid_symbols']
        
        # 保存到状态
        self.state.update_temporary_data('clean_data', clean_data)
        self.state.update_temporary_data('valid_symbols', valid_symbols)
        
        # 如果有效股票不足，返回空结果
        if len(valid_symbols) < 2:
            return {
                'modeled_pairs': [],
                'statistics': {'status': 'insufficient_symbols'}
            }
        
        # 步骤2: 协整分析
        cointegration_result = self.cointegration_analyzer.analyze(
            valid_symbols, 
            clean_data, 
            self.sector_code_to_name
        )
        
        cointegrated_pairs = cointegration_result['cointegrated_pairs']
        self.state.update_temporary_data('cointegrated_pairs', cointegrated_pairs)
        
        # 如果没有协整对，返回空结果
        if not cointegrated_pairs:
            return {
                'modeled_pairs': [],
                'statistics': {'status': 'no_cointegrated_pairs'}
            }
        
        # 步骤3: 贝叶斯建模
        modeling_result = self.bayesian_modeler.model_pairs(
            cointegrated_pairs,
            clean_data
        )
        
        return {
            'modeled_pairs': modeling_result['modeled_pairs'],
            'statistics': {
                'data_processing': data_result.get('statistics', {}),
                'cointegration': cointegration_result.get('statistics', {}),
                'modeling': modeling_result.get('statistics', {})
            }
        }