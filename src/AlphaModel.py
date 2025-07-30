# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import itertools
from statsmodels.tsa.stattools import coint
import pymc as pm
from datetime import timedelta
# endregion


# =============================================================================
# 数据处理模块 - DataProcessor
# =============================================================================
class DataProcessor:
    """
    数据处理器类
    封装数据下载、检查、清理和筛选的完整流程
    """
    
    def __init__(self, algorithm, config: dict):
        """
        初始化数据处理器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含数据处理相关配置的字典
        """
        self.algorithm = algorithm
        self.lookback_period = config['lookback_period']
        self.min_data_completeness_ratio = config['min_data_completeness_ratio']
        self.max_annual_volatility = config['max_annual_volatility']
        self.volatility_window_days = config['volatility_window_days']
    
    def process(self, symbols: List[Symbol]) -> Dict:
        """
        执行完整的数据处理流程
        
        处理步骤:
        1. 下载历史数据
        2. 数据完整性检查 (98%规则)
        3. 数据合理性检查 (无负值)
        4. 空缺填补
        5. 波动率筛选
        
        Args:
            symbols: 待处理的股票列表
            
        Returns:
            dict: 包含clean_data, valid_symbols和statistics的字典
        """
        # 初始化统计
        statistics = {
            'total': len(symbols),
            'data_missing': 0,
            'incomplete': 0,
            'invalid_values': 0,
            'high_volatility': 0,
            'final_valid': 0
        }
        
        # 步骤1: 下载历史数据
        all_histories = self._download_historical_data(symbols)
        if all_histories is None:
            return {
                'clean_data': {},
                'valid_symbols': [],
                'statistics': statistics
            }
        
        # 步骤2-5: 处理每个symbol
        clean_data = {}
        valid_symbols = []
        
        for symbol in symbols:
            # 检查数据是否存在
            if symbol not in all_histories.index.get_level_values(0):
                statistics['data_missing'] += 1
                continue
            
            try:
                symbol_data = all_histories.loc[symbol]
                
                # 检查是否有close列
                if 'close' not in symbol_data.columns:
                    statistics['data_missing'] += 1
                    continue
                
                # 步骤2: 数据完整性检查
                if not self._check_data_completeness(symbol_data):
                    statistics['incomplete'] += 1
                    continue
                
                # 步骤3: 数据合理性检查
                if not self._check_data_validity(symbol_data['close']):
                    statistics['invalid_values'] += 1
                    continue
                
                # 步骤4: 空缺填补
                symbol_data = self._fill_missing_values(symbol_data)
                
                # 步骤5: 波动率检查
                if not self._check_volatility(symbol_data['close']):
                    statistics['high_volatility'] += 1
                    continue
                
                # 通过所有检查
                clean_data[symbol] = symbol_data
                valid_symbols.append(symbol)
                
            except Exception as e:
                self.algorithm.Debug(f"[AlphaModel.Data] 处理{symbol.Value}时出错: {str(e)[:50]}")
                statistics['data_missing'] += 1
        
        statistics['final_valid'] = len(valid_symbols)
        
        # 输出统计信息
        self._log_statistics(statistics)
        
        return {
            'clean_data': clean_data,
            'valid_symbols': valid_symbols,
            'statistics': statistics
        }
    
    def _download_historical_data(self, symbols: List[Symbol]):
        """
        下载历史数据
        
        Args:
            symbols: 股票列表
            
        Returns:
            DataFrame或None
        """
        try:
            return self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel.Data] 历史数据下载失败: {str(e)}")
            return None
    
    def _check_data_completeness(self, data: pd.DataFrame) -> bool:
        """
        检查数据完整性
        
        Args:
            data: 股票数据DataFrame
            
        Returns:
            bool: 是否通过完整性检查
        """
        required_length = int(self.lookback_period * self.min_data_completeness_ratio)
        return len(data) >= required_length
    
    def _check_data_validity(self, close_prices: pd.Series) -> bool:
        """
        检查数据合理性（无负值或零值）
        
        Args:
            close_prices: 收盘价序列
            
        Returns:
            bool: 是否通过合理性检查
        """
        return not (close_prices <= 0).any()
    
    def _fill_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        填补空缺值
        
        Args:
            data: 股票数据DataFrame
            
        Returns:
            DataFrame: 填补后的数据
        """
        if data['close'].isnull().any():
            data['close'] = data['close'].fillna(method='pad').fillna(method='bfill')
        return data
    
    def _check_volatility(self, close_prices: pd.Series) -> bool:
        """
        检查波动率是否在可接受范围内
        
        Args:
            close_prices: 收盘价序列
            
        Returns:
            bool: 是否通过波动率检查
        """
        recent_data = close_prices.tail(self.volatility_window_days)
        returns = recent_data.pct_change().dropna()
        
        daily_volatility = returns.std()
        annual_volatility = daily_volatility * np.sqrt(252)
        
        return annual_volatility <= self.max_annual_volatility
    
    def _log_statistics(self, statistics: dict):
        """输出统计信息"""
        self.algorithm.Debug(
            f"[AlphaModel.Data] 数据处理完成: {statistics['total']}只 → {statistics['final_valid']}只 "
            f"(缺失{statistics['data_missing']}只, "
            f"不完整{statistics['incomplete']}只, "
            f"无效值{statistics['invalid_values']}只, "
            f"高波动{statistics['high_volatility']}只)"
        )


# =============================================================================
# 协整分析模块 - CointegrationAnalyzer
# =============================================================================
class CointegrationAnalyzer:
    """
    协整分析器类
    负责行业分组、配对生成和协整检验
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
            
            # 更新统计
            statistics['sector_breakdown'][sector_name] = {
                'symbols': len(symbols),
                'pairs_tested': len(list(itertools.combinations(symbols, 2))),
                'pairs_found': len(sector_pairs)
            }
            statistics['total_pairs_tested'] += len(list(itertools.combinations(symbols, 2)))
        
        # 3. 筛选配对（限制每个股票出现次数）
        filtered_pairs = self._filter_pairs(all_cointegrated_pairs)
        statistics['cointegrated_pairs_found'] = len(filtered_pairs)
        
        # 输出统计信息
        self._log_statistics(statistics)
        
        return {
            'cointegrated_pairs': filtered_pairs,
            'statistics': statistics
        }
    
    def _group_by_sector(self, valid_symbols: List[Symbol], sector_code_to_name: Dict) -> Dict[str, List[Symbol]]:
        """
        将股票按行业分组
        
        Args:
            valid_symbols: 有效股票列表
            sector_code_to_name: 行业映射
            
        Returns:
            {行业名称: [股票列表]}
        """
        sector_to_symbols = defaultdict(list)
        
        for symbol in valid_symbols:
            # 获取股票的行业分类信息
            security = self.algorithm.Securities[symbol]
            sector_code = security.Fundamentals.AssetClassification.MorningstarSectorCode
            sector_name = sector_code_to_name.get(sector_code)
            
            if sector_name:
                sector_to_symbols[sector_name].append(symbol)
        
        # 输出分组信息
        return dict(sector_to_symbols)
    
    def _analyze_sector(self, sector_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        分析单个行业内的协整关系
        
        Args:
            sector_name: 行业名称
            symbols: 该行业的股票列表
            clean_data: 清洗后的数据
            
        Returns:
            协整对列表
        """
        cointegrated_pairs = []
        
        # 生成该行业内所有可能的股票配对组合
        for symbol1, symbol2 in itertools.combinations(symbols, 2):
            # 获取价格数据
            prices1 = clean_data[symbol1]['close']
            prices2 = clean_data[symbol2]['close']
            
            # 执行协整检验
            try:
                # Engle-Granger协整检验，检验两个时间序列是否存在长期均衡关系
                score, pvalue, _ = coint(prices1, prices2)
                
                # 计算皮尔逊相关系数，确保两个股票有足够的相关性
                correlation = prices1.corr(prices2)
                
                # 判断是否同时满足协整性和相关性要求
                if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                    cointegrated_pairs.append({
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'pvalue': pvalue,
                        'correlation': correlation,
                        'sector': sector_name
                    })
            except Exception as e:
                self.algorithm.Debug(
                    f"[AlphaModel.Coint] 协整检验失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}"
                )
        
        return cointegrated_pairs
    
    def _filter_pairs(self, all_pairs: List[Dict]) -> List[Dict]:
        """
        筛选配对，限制每个股票的出现次数
        
        Args:
            all_pairs: 所有通过协整检验的配对
            
        Returns:
            筛选后的配对列表
        """
        # 按照p-value从小到大排序，优先选择协整性最强的配对
        sorted_pairs = sorted(all_pairs, key=lambda x: x['pvalue'])
        
        # 统计每个股票出现的次数
        symbol_count = defaultdict(int)
        filtered_pairs = []
        
        for pair in sorted_pairs:
            symbol1 = pair['symbol1']
            symbol2 = pair['symbol2']
            
            # 确保每个股票不会出现在过多的配对中，避免风险集中
            if (symbol_count[symbol1] < self.max_symbol_repeats and 
                symbol_count[symbol2] < self.max_symbol_repeats and
                len(filtered_pairs) < self.max_pairs):
                
                filtered_pairs.append(pair)
                symbol_count[symbol1] += 1
                symbol_count[symbol2] += 1
        
        return filtered_pairs
    
    def _log_statistics(self, statistics: dict):
        """输出统计信息"""
        # 只输出有发现配对的行业
        sector_summary = []
        for sector, stats in statistics['sector_breakdown'].items():
            if stats['pairs_found'] > 0:
                sector_summary.append(f"{sector}({stats['pairs_found']})")
        
        # 输出简洁的统计信息
        if sector_summary:
            self.algorithm.Debug(
                f"[AlphaModel.Coint] {' '.join(sector_summary)} - 筛选后{statistics['cointegrated_pairs_found']}对"
            )


# =============================================================================
# 贝叶斯建模模块 - BayesianModeler
# =============================================================================
class BayesianModeler:
    """
    贝叶斯建模器类
    使用PyMC进行贝叶斯线性回归建模
    支持完全建模和动态更新两种模式
    """
    
    def __init__(self, algorithm, config: dict):
        """
        初始化贝叶斯建模器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含MCMC相关配置的字典
        """
        self.algorithm = algorithm
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        self.lookback_period = config['lookback_period']
        
        # 历史后验存储 {(symbol1, symbol2): {...}}
        self.historical_posteriors = {}
    
    def model_pairs(self, cointegrated_pairs: List[Dict], clean_data: Dict) -> Dict:
        """
        对所有协整对进行贝叶斯建模
        
        Args:
            cointegrated_pairs: 协整对列表
            clean_data: 清洗后的数据
            
        Returns:
            包含建模结果和统计信息的字典
        """
        modeled_pairs = []
        statistics = {
            'total_pairs': len(cointegrated_pairs),
            'successful': 0,
            'failed': 0,
            'full_modeling': 0,
            'dynamic_update': 0
        }
        
        # 对每个协整对进行建模
        for pair in cointegrated_pairs:
            result = self._model_single_pair(pair, clean_data)
            if result:
                modeled_pairs.append(result)
                statistics['successful'] += 1
                
                # 更新统计
                if result['modeling_type'] == 'dynamic':
                    statistics['dynamic_update'] += 1
                else:
                    statistics['full_modeling'] += 1
            else:
                statistics['failed'] += 1
        
        # 输出统计信息
        self._log_statistics(statistics)
        
        return {
            'modeled_pairs': modeled_pairs,
            'statistics': statistics
        }
    
    def _model_single_pair(self, pair: Dict, clean_data: Dict) -> Dict:
        """
        处理单个配对的完整流程
        
        Args:
            pair: 协整对信息
            clean_data: 清洗后的数据
            
        Returns:
            建模结果字典，失败返回None
        """
        try:
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']
            
            # 提取价格数据
            prices1 = clean_data[symbol1]['close'].values
            prices2 = clean_data[symbol2]['close'].values
            
            # 获取历史后验（如果存在）
            prior_params = self._get_prior_params(symbol1, symbol2)
            
            # 执行贝叶斯建模和采样
            trace = self._build_and_sample_model(prices1, prices2, prior_params)
            
            # 构建结果（使用展开操作符避免冗余）
            result = {
                **self._extract_posterior_stats(trace),
                'symbol1': symbol1,
                'symbol2': symbol2,
                'sector': pair['sector'],
                'modeling_type': 'dynamic' if prior_params else 'full',
                'modeling_time': self.algorithm.Time
            }
            
            # 保存后验用于未来的动态更新
            self._save_posterior(symbol1, symbol2, result)
            
            return result
            
        except Exception as e:
            self.algorithm.Debug(
                f"[AlphaModel.Bayesian] 建模失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}"
            )
            return None
    
    def _get_prior_params(self, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """
        获取历史后验参数（如果存在且有效）
        
        Args:
            symbol1, symbol2: 配对的两个股票
            
        Returns:
            历史后验参数字典，如果不存在或过期则返回None
        """
        pair_key = (symbol1, symbol2)
        
        # 检查是否有历史记录
        if pair_key not in self.historical_posteriors:
            return None
        
        # 检查时间有效性
        historical = self.historical_posteriors[pair_key]
        time_diff = self.algorithm.Time - historical['update_time']
        
        # 如果超过lookback期限，视为过期
        if time_diff.days > self.lookback_period:
            self.algorithm.Debug(
                f"[AlphaModel.Bayesian] 历史后验已过期 {symbol1.Value}-{symbol2.Value} "
                f"(距今{time_diff.days}天)"
            )
            return None
        
        return historical
    
    def _build_and_sample_model(self, prices1: np.ndarray, prices2: np.ndarray, 
                                prior_params: Dict = None) -> pm.backends.base.MultiTrace:
        """
        构建贝叶斯线性回归模型并执行MCMC采样
        
        模型: log(price1) = alpha + beta * log(price2) + epsilon
        
        Args:
            prices1: 股票1的价格数据
            prices2: 股票2的价格数据
            prior_params: 历史后验参数（用作先验）
            
        Returns:
            MCMC采样结果
        """
        # 对数转换
        x_data = np.log(prices2)
        y_data = np.log(prices1)
        
        with pm.Model() as model:
            # 设置先验
            if prior_params is None:
                # 完全建模：使用弱信息先验
                alpha = pm.Normal('alpha', mu=0, sigma=10)
                beta = pm.Normal('beta', mu=1, sigma=5)
                sigma = pm.HalfNormal('sigma', sigma=1)
                
                # 标准采样参数
                tune = self.mcmc_warmup_samples
                draws = self.mcmc_posterior_samples
            else:
                # 动态更新：使用历史后验作为先验
                alpha = pm.Normal('alpha', 
                                mu=prior_params['alpha_mean'], 
                                sigma=prior_params['alpha_std'])
                beta = pm.Normal('beta', 
                               mu=prior_params['beta_mean'], 
                               sigma=prior_params['beta_std'])
                
                # 对于sigma，使用HalfNormal（简单稳健）
                sigma = pm.HalfNormal('sigma', sigma=prior_params['sigma_mean'])
                
                # 减少采样数量以提高效率
                tune = self.mcmc_warmup_samples // 2
                draws = self.mcmc_posterior_samples // 2
            
            # 定义线性关系
            mu = alpha + beta * x_data
            
            # 定义似然函数
            likelihood = pm.Normal('y', mu=mu, sigma=sigma, observed=y_data)
            
            # 计算残差（用于后续分析）
            residuals = pm.Deterministic('residuals', y_data - mu)
            
            # 执行MCMC采样
            trace = pm.sample(
                draws=draws, 
                tune=tune, 
                chains=self.mcmc_chains,
                return_inferencedata=False,
                progressbar=False  # 避免在QuantConnect中显示进度条
            )
        
        return trace
    
    def _extract_posterior_stats(self, trace) -> Dict:
        """
        从MCMC采样结果中提取后验统计量
        
        Args:
            trace: PyMC采样结果
            
        Returns:
            包含所有需要的后验统计量的字典
        """
        # 提取样本
        alpha_samples = trace['alpha'].flatten()
        beta_samples = trace['beta'].flatten()
        sigma_samples = trace['sigma'].flatten()
        residuals_samples = trace['residuals'].flatten()  # 展平所有残差值
        
        # 计算统计量
        return {
            'alpha_mean': float(alpha_samples.mean()),
            'alpha_std': float(alpha_samples.std()),
            'beta_mean': float(beta_samples.mean()),
            'beta_std': float(beta_samples.std()),
            'sigma_mean': float(sigma_samples.mean()),
            'sigma_std': float(sigma_samples.std()),
            'residual_mean': float(residuals_samples.mean()),
            'residual_std': float(sigma_samples.mean())  # 使用sigma而不是residuals.std()
        }
    
    def _save_posterior(self, symbol1: Symbol, symbol2: Symbol, stats: Dict):
        """
        保存后验参数用于未来的动态更新
        
        Args:
            symbol1, symbol2: 配对的股票
            stats: 包含后验统计量的字典
        """
        pair_key = (symbol1, symbol2)
        
        # 保存需要的统计量和更新时间
        self.historical_posteriors[pair_key] = {
            'alpha_mean': stats['alpha_mean'],
            'alpha_std': stats['alpha_std'],
            'beta_mean': stats['beta_mean'],
            'beta_std': stats['beta_std'],
            'sigma_mean': stats['sigma_mean'],
            'sigma_std': stats['sigma_std'],
            'update_time': self.algorithm.Time
        }
    
    def _log_statistics(self, statistics: dict):
        """输出建模统计信息"""
        self.algorithm.Debug(
            f"[AlphaModel.Bayesian] 建模完成: "
            f"成功{statistics['successful']}对, "
            f"失败{statistics['failed']}对 "
            f"(完全建模{statistics['full_modeling']}对, "
            f"动态更新{statistics['dynamic_update']}对)"
        )


# =============================================================================
# 信号生成模块 - SignalGenerator
# =============================================================================
class SignalGenerator:
    """
    信号生成器类
    基于贝叶斯建模结果计算z-score并生成交易信号
    """
    
    def __init__(self, algorithm, config: dict):
        """
        初始化信号生成器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含信号生成相关配置的字典
        """
        self.algorithm = algorithm
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.upper_limit = config['upper_limit']
        self.lower_limit = config['lower_limit']
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 1)
        self.entry_signal_duration_days = config.get('entry_signal_duration_days', 2)
    
    def generate_signals(self, modeled_pairs: List[Dict], data) -> List:
        """
        为所有建模配对生成交易信号
        
        Args:
            modeled_pairs: 建模结果列表
            data: 当前市场数据
            
        Returns:
            Insight列表
        """
        insights = []
        
        for pair in modeled_pairs:
            # 计算当前z-score
            pair_with_zscore = self._calculate_zscore(pair, data)
            if pair_with_zscore is None:
                continue
            
            # 生成信号
            pair_insights = self._generate_pair_signals(pair_with_zscore)
            insights.extend(pair_insights)
        
        return insights
    
    def _calculate_zscore(self, pair: Dict, data) -> Dict:
        """
        计算配对的当前z-score
        
        Args:
            pair: 包含建模参数的配对信息
            data: 当前市场数据
            
        Returns:
            添加了z-score的配对信息
        """
        symbol1 = pair['symbol1']
        symbol2 = pair['symbol2']
        
        # 获取当前价格
        if not (data.ContainsKey(symbol1) and data.ContainsKey(symbol2)):
            return None
        
        if not (data[symbol1] and data[symbol2]):
            return None
        
        current_price1 = float(data[symbol1].Close)
        current_price2 = float(data[symbol2].Close)
        
        # 使用对数价格计算
        log_price1 = np.log(current_price1)
        log_price2 = np.log(current_price2)
        
        # 提取贝叶斯参数
        alpha_mean = pair['alpha_mean']
        beta_mean = pair['beta_mean']
        residual_mean = pair['residual_mean']
        residual_std = pair['residual_std']
        
        # 计算期望价格和残差
        expected_log_price1 = alpha_mean + beta_mean * log_price2
        residual = log_price1 - expected_log_price1 - residual_mean
        
        # 计算z-score
        if residual_std > 0:
            zscore = residual / residual_std
        else:
            zscore = 0
        
        # 更新配对信息
        pair['zscore'] = zscore
        pair['current_price1'] = current_price1
        pair['current_price2'] = current_price2
        
        # 调试日志
        self.algorithm.Debug(
            f"[AlphaModel.Signal] {symbol1.Value}-{symbol2.Value}: "
            f"z-score={zscore:.3f}, residual_std={residual_std:.4f}"
        )
        
        return pair
    
    def _generate_pair_signals(self, pair: Dict) -> List:
        """
        基于z-score为单个配对生成信号
        
        Args:
            pair: 包含z-score的配对信息
            
        Returns:
            Insight列表
        """
        insights = []
        
        symbol1 = pair['symbol1']
        symbol2 = pair['symbol2']
        zscore = pair['zscore']
        
        # 构建标签信息
        tag = f"{symbol1.Value}&{symbol2.Value}|{pair['alpha_mean']:.4f}|{pair['beta_mean']:.4f}|{zscore:.2f}"
        
        # 风险检查 - 极端偏离时立即平仓
        if abs(zscore) > self.upper_limit:
            # 生成平仓信号
            pair_insights = Insight.Group(
                Insight.Price(
                    symbol1, 
                    timedelta(days=self.flat_signal_duration_days),
                    InsightDirection.Flat,
                    None, None, None,
                    None,
                    tag
                ),
                Insight.Price(
                    symbol2,
                    timedelta(days=self.flat_signal_duration_days),
                    InsightDirection.Flat,
                    None, None, None,
                    None,
                    tag
                )
            )
            insights.extend(pair_insights)
            return insights
        
        # 正常信号生成
        if abs(zscore) > self.entry_threshold:
            # 建仓信号
            if zscore > self.entry_threshold:
                # z > 1.65: 股票1高估，做空1做多2
                direction1 = InsightDirection.Down
                direction2 = InsightDirection.Up
            else:
                # z < -1.65: 股票1低估，做多1做空2
                direction1 = InsightDirection.Up
                direction2 = InsightDirection.Down
            
            pair_insights = Insight.Group(
                Insight.Price(
                    symbol1,
                    timedelta(days=self.entry_signal_duration_days),
                    direction1,
                    None, None, None,
                    None,
                    tag
                ),
                Insight.Price(
                    symbol2,
                    timedelta(days=self.entry_signal_duration_days),
                    direction2,
                    None, None, None,
                    None,
                    tag
                )
            )
            insights.extend(pair_insights)
            
        elif abs(zscore) < self.exit_threshold:
            # 平仓信号
            pair_insights = Insight.Group(
                Insight.Price(
                    symbol1,
                    timedelta(days=self.flat_signal_duration_days),
                    InsightDirection.Flat,
                    None, None, None,
                    None,
                    tag
                ),
                Insight.Price(
                    symbol2,
                    timedelta(days=self.flat_signal_duration_days),
                    InsightDirection.Flat,
                    None, None, None,
                    None,
                    tag
                )
            )
            insights.extend(pair_insights)
        
        return insights


# =============================================================================
# 主Alpha模型 - BayesianCointegrationAlphaModel
# =============================================================================
class BayesianCointegrationAlphaModel(AlphaModel):
    """
    贝叶斯协整Alpha模型
    
    清晰的数据处理流程和结构化的实现
    """
    
    def __init__(self, algorithm, config: dict, pair_ledger, sector_code_to_name: dict):
        """
        初始化Alpha模型
        
        Args:
            algorithm: QuantConnect算法实例
            config: 配置字典
            pair_ledger: 配对记账簿实例
            sector_code_to_name: 行业代码到名称的映射
        """
        super().__init__()
        self.algorithm = algorithm
        self.config = config
        self.pair_ledger = pair_ledger
        self.sector_code_to_name = sector_code_to_name
        
        # 状态管理
        self.is_selection_day = False
        self.symbols = []
        
        # 数据存储
        self.clean_data = {}  # 处理后的干净数据
        self.valid_symbols = []  # 通过所有筛选的股票
        self.cointegrated_pairs = []  # 协整对结果
        self.modeled_pairs = []  # 贝叶斯建模结果
        
        # 创建数据处理器
        self.data_processor = DataProcessor(self.algorithm, self.config)
        
        # 创建协整分析器
        self.cointegration_analyzer = CointegrationAnalyzer(self.algorithm, self.config)
        
        # 创建贝叶斯建模器
        self.bayesian_modeler = BayesianModeler(self.algorithm, self.config)
        
        # 创建信号生成器
        self.signal_generator = SignalGenerator(self.algorithm, self.config)
        
        self.algorithm.Debug(f"[AlphaModel] 初始化完成")
    
    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        处理证券变更事件
        步骤1: 解析选股结果
        """
        self.is_selection_day = True
        
        # 添加新股票
        for added_security in changes.AddedSecurities:
            symbol = added_security.Symbol
            if symbol and symbol not in self.symbols:
                self.symbols.append(symbol)
        
        # 移除旧股票
        for removed_security in changes.RemovedSecurities:
            symbol = removed_security.Symbol
            if symbol and symbol in self.symbols:
                self.symbols.remove(symbol)
        
    
    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        """
        主更新方法
        """
        if not self.symbols or len(self.symbols) < 2:
            return []
        
        if self.is_selection_day:
            # 使用数据处理器处理数据
            data_result = self.data_processor.process(self.symbols)
            
            # 保存处理结果
            self.clean_data = data_result['clean_data']
            self.valid_symbols = data_result['valid_symbols']
            
            # 协整分析
            if len(self.valid_symbols) >= 2:
                cointegration_result = self.cointegration_analyzer.analyze(
                    self.valid_symbols, 
                    self.clean_data, 
                    self.sector_code_to_name
                )
                
                # 保存协整对结果
                self.cointegrated_pairs = cointegration_result['cointegrated_pairs']
                
                # 更新配对记账簿
                pairs_list = [(pair['symbol1'], pair['symbol2']) for pair in self.cointegrated_pairs]
                self.pair_ledger.update_pairs_from_selection(pairs_list)
                
                # 贝叶斯建模
                if len(self.cointegrated_pairs) > 0:
                    modeling_result = self.bayesian_modeler.model_pairs(
                        self.cointegrated_pairs,
                        self.clean_data
                    )
                    
                    # 保存建模结果
                    self.modeled_pairs = modeling_result['modeled_pairs']
                    
            
            self.is_selection_day = False
        
        # 日常信号生成
        if self.modeled_pairs:
            return self.signal_generator.generate_signals(self.modeled_pairs, data)
        
        return []