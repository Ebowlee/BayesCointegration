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
        # 使用defaultdict简化统计
        statistics = defaultdict(int, total=len(symbols))
        
        # 下载历史数据
        all_histories = self._download_historical_data(symbols)
        if all_histories is None:
            return {'clean_data': {}, 'valid_symbols': [], 'statistics': dict(statistics)}
        
        clean_data = {}
        valid_symbols = []
        
        # 处理每个symbol
        for symbol in symbols:
            result = self._process_symbol(symbol, all_histories, statistics)
            if result is not None:
                clean_data[symbol] = result
                valid_symbols.append(symbol)
        
        statistics['final_valid'] = len(valid_symbols)
        self._log_statistics(dict(statistics))
        
        return {
            'clean_data': clean_data,
            'valid_symbols': valid_symbols,
            'statistics': dict(statistics)
        }
    
    def _process_symbol(self, symbol: Symbol, all_histories, statistics: dict):
        """
        处理单个股票，返回处理后的数据或None
        """
        try:
            # 检查数据存在性
            if symbol not in all_histories.index.get_level_values(0):
                statistics['data_missing'] += 1
                return None
            
            symbol_data = all_histories.loc[symbol]
            
            # 统一的数据验证
            is_valid, reason = self._validate_data(symbol_data)
            if not is_valid:
                statistics[reason] += 1
                return None
            
            # 填补缺失值
            symbol_data = self._fill_missing_values(symbol_data)
            
            # 波动率检查
            if not self._check_volatility(symbol_data['close']):
                statistics['high_volatility'] += 1
                return None
            
            return symbol_data
            
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel.Data] 处理{symbol.Value}时出错: {str(e)[:50]}")
            statistics['data_missing'] += 1
            return None
    
    def _download_historical_data(self, symbols: List[Symbol]):
        """
        下载历史数据
        """
        try:
            return self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel.Data] 历史数据下载失败: {str(e)}")
            return None
    
    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """
        验证数据完整性和合理性
        
        Returns:
            (是否有效, 失败原因)
        """
        # 检查close列
        if 'close' not in data.columns:
            return False, 'data_missing'
        
        # 检查完整性
        required_length = int(self.lookback_period * self.min_data_completeness_ratio)
        if len(data) < required_length:
            return False, 'incomplete'
        
        # 检查合理性
        if (data['close'] <= 0).any():
            return False, 'invalid_values'
        
        return True, ''
    
    def _fill_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        填补空缺值
        """
        if data['close'].isnull().any():
            # 一次性完成所有填充操作
            data['close'] = (data['close']
                            .interpolate(method='linear', limit_direction='both')
                            .fillna(method='pad')
                            .fillna(method='bfill'))
        return data
    
    def _check_volatility(self, close_prices: pd.Series) -> bool:
        """
        检查波动率是否在可接受范围内
        """
        recent_data = close_prices.tail(self.volatility_window_days)
        returns = recent_data.pct_change().dropna()
        
        daily_volatility = returns.std()
        annual_volatility = daily_volatility * np.sqrt(252)
        
        return annual_volatility <= self.max_annual_volatility
    
    def _log_statistics(self, stats: dict):
        """
        输出统计信息
        """
        failed_details = [
            f"{reason}({count})" 
            for reason, count in stats.items() 
            if reason not in ['total', 'final_valid'] and count > 0
        ]
        
        self.algorithm.Debug(
            f"[AlphaModel.Data] 数据处理: {stats['total']}→{stats['final_valid']}只 ({', '.join(failed_details)})"
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
        
        # 输出统计信息
        self._log_statistics(statistics)
        
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
        for symbol1, symbol2 in itertools.combinations(symbols, 2):
            prices1 = clean_data[symbol1]['close']
            prices2 = clean_data[symbol2]['close']
            
            # 测试配对协整性
            pair_result = self._test_pair_cointegration(
                symbol1, symbol2, prices1, prices2, sector_name
            )
            if pair_result:
                cointegrated_pairs.append(pair_result)
        
        return cointegrated_pairs
    
    def _test_pair_cointegration(self, symbol1: Symbol, symbol2: Symbol, 
                                prices1: pd.Series, prices2: pd.Series, 
                                sector_name: str) -> Dict:
        """
        测试单个配对的协整性
        """
        try:
            # Engle-Granger协整检验
            score, pvalue, _ = coint(prices1, prices2)
            
            # 计算相关系数
            correlation = prices1.corr(prices2)
            
            # 检查是否满足条件
            if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                return {
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'pvalue': pvalue,
                    'correlation': correlation,
                    'sector': sector_name
                }
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel.Coint] 协整检验失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")
        return None
    
    def _filter_pairs(self, all_pairs: List[Dict]) -> List[Dict]:
        """
        筛选配对，限制每个股票的出现次数
        """
        sorted_pairs = sorted(all_pairs, key=lambda x: x['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = []
        
        for pair in sorted_pairs:
            if len(filtered_pairs) >= self.max_pairs:
                break
                
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']
            if (symbol_count[symbol1] < self.max_symbol_repeats and 
                symbol_count[symbol2] < self.max_symbol_repeats):
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
            self.algorithm.Debug(f"[AlphaModel.Coint] {' '.join(sector_summary)} - 筛选后{statistics['cointegrated_pairs_found']}对")


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
            trace, x_data, y_data = self._build_and_sample_model(prices1, prices2, prior_params)
            
            # 构建结果（使用展开操作符避免冗余）
            result = {
                **self._extract_posterior_stats(trace, x_data, y_data),
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
            self.algorithm.Debug(f"[AlphaModel.Bayesian] 建模失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")
            return None
    
    def _get_prior_params(self, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """
        获取历史后验参数
        """
        pair_key = (symbol1, symbol2)
        
        # 一次性检查存在性和有效性
        if pair_key not in self.historical_posteriors:
            return None
        
        historical = self.historical_posteriors[pair_key]
        time_diff = self.algorithm.Time - historical['update_time']
        
        if time_diff.days > self.lookback_period:
            self.algorithm.Debug(f"[AlphaModel.Bayesian] 历史后验已过期 {symbol1.Value}-{symbol2.Value} ({time_diff.days}天)")
            return None
        
        return historical
    
    def _build_and_sample_model(self, prices1: np.ndarray, prices2: np.ndarray, 
                                prior_params: Dict = None) -> Tuple[pm.backends.base.MultiTrace, np.ndarray, np.ndarray]:
        """
        构建贝叶斯线性回归模型并执行MCMC采样
        
        模型: log(price1) = alpha + beta * log(price2) + epsilon
        
        Args:
            prices1: 股票1的价格数据
            prices2: 股票2的价格数据
            prior_params: 历史后验参数（用作先验）
            
        Returns:
            (MCMC采样结果, x_data, y_data)
        """
        # 对数转换
        x_data = np.log(prices2)
        y_data = np.log(prices1)
        
        
        with pm.Model() as model:
            # 设置先验和采样参数
            priors = self._setup_priors(prior_params)
            alpha = priors['alpha']
            beta = priors['beta']
            sigma = priors['sigma']
            tune = priors['tune']
            draws = priors['draws']
            
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
        
        
        return trace, x_data, y_data
    
    def _setup_priors(self, prior_params: Dict):
        """
        设置模型先验
        """
        if prior_params is None:
            return {
                'alpha': pm.Normal('alpha', mu=0, sigma=10),
                'beta': pm.Normal('beta', mu=1, sigma=5),
                'sigma': pm.HalfNormal('sigma', sigma=5.0),
                'tune': self.mcmc_warmup_samples,
                'draws': self.mcmc_posterior_samples
            }
        else:
            return {
                'alpha': pm.Normal('alpha', mu=prior_params['alpha_mean'], sigma=prior_params['alpha_std']),
                'beta': pm.Normal('beta', mu=prior_params['beta_mean'], sigma=prior_params['beta_std']),
                'sigma': pm.HalfNormal('sigma', sigma=max(prior_params['sigma_mean'] * 1.5, 1.0)),
                'tune': self.mcmc_warmup_samples // 2,
                'draws': self.mcmc_posterior_samples // 2
            }
    
    def _extract_posterior_stats(self, trace, x_data, y_data) -> Dict:
        """
        从MCMC采样结果中提取后验统计量
        
        Args:
            trace: MCMC采样结果
            x_data: 对数转换后的自变量数据（用于计算实际残差）
            y_data: 对数转换后的因变量数据（用于计算实际残差）
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
        
        # 方法2：使用后验均值参数计算实际残差的标准差
        alpha_mean = stats['alpha_mean']
        beta_mean = stats['beta_mean']
        fitted_values = alpha_mean + beta_mean * x_data
        actual_residuals = y_data - fitted_values
        
        # 移除最小值限制，使用实际计算的标准差
        stats['residual_std'] = float(np.std(actual_residuals))
        
        # 添加诊断日志
        self.algorithm.Debug(
            f"[AlphaModel.Bayesian] Residual计算: "
            f"实际std={stats['residual_std']:.4f}, "
            f"sigma均值={stats['sigma_mean']:.4f}"
        )
        
        return stats
    
    def _save_posterior(self, symbol1: Symbol, symbol2: Symbol, stats: Dict):
        """
        保存后验参数
        """
        pair_key = (symbol1, symbol2)
        
        # 只保存需要的统计量
        keys_to_save = ['alpha_mean', 'alpha_std', 'beta_mean', 'beta_std', 'sigma_mean', 'sigma_std']
        self.historical_posteriors[pair_key] = {
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
        # self.upper_limit = config['upper_limit']  # 移除，交给风险管理模块
        self.lower_limit = config['lower_limit']
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 1)
        self.entry_signal_duration_days = config.get('entry_signal_duration_days', 2)
        
        # 添加EMA相关参数
        self.zscore_ema = {}  # 存储每个配对的EMA值
        self.ema_alpha = 0.8  # EMA平滑系数（80%当前值，20%历史值）
    
    def generate_signals(self, modeled_pairs: List[Dict], data) -> List:
        """
        为所有建模配对生成交易信号
        """
        insights = []
        
        for pair in modeled_pairs:
            pair_with_zscore = self._calculate_zscore(pair, data)
            if pair_with_zscore:
                insights.extend(self._generate_pair_signals(pair_with_zscore))
        
        return insights
    
    def _calculate_zscore(self, pair: Dict, data) -> Dict:
        """
        计算配对的当前z-score（添加EMA平滑）
        """
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']
        
        # 验证并获取价格
        if not all([data.ContainsKey(s) and data[s] for s in [symbol1, symbol2]]):
            return None
        
        current_price1 = float(data[symbol1].Close)
        current_price2 = float(data[symbol2].Close)
        
        # 计算原始z-score
        log_price1 = np.log(current_price1)
        log_price2 = np.log(current_price2)
        
        expected = pair['alpha_mean'] + pair['beta_mean'] * log_price2
        residual = log_price1 - expected - pair['residual_mean']
        
        raw_zscore = residual / pair['residual_std'] if pair['residual_std'] > 0 else 0
        
        # EMA平滑处理
        pair_key = (symbol1, symbol2)
        if pair_key not in self.zscore_ema:
            # 首次计算，直接使用原始值
            smoothed_zscore = raw_zscore
        else:
            # 应用EMA平滑
            smoothed_zscore = self.ema_alpha * raw_zscore + (1 - self.ema_alpha) * self.zscore_ema[pair_key]
        
        # 更新EMA存储
        self.zscore_ema[pair_key] = smoothed_zscore
        
        # 更新配对信息
        pair.update({
            'zscore': smoothed_zscore,  # 使用平滑后的值
            'raw_zscore': raw_zscore,   # 保留原始值供参考
            'current_price1': current_price1,
            'current_price2': current_price2
        })
        
        self.algorithm.Debug(
            f"[AlphaModel.Signal] {symbol1.Value}-{symbol2.Value}: "
            f"z-score={smoothed_zscore:.3f} (raw={raw_zscore:.3f}), "
            f"residual_std={pair['residual_std']:.4f}"
        )
        
        return pair
    
    def _create_insight_group(self, symbol1: Symbol, symbol2: Symbol, 
                             direction1: InsightDirection, direction2: InsightDirection,
                             duration_days: int, tag: str) -> List:
        """
        创建配对的Insight组
        """
        return Insight.Group(
            Insight.Price(symbol1, timedelta(days=duration_days), direction1, 
                         None, None, None, None, tag),
            Insight.Price(symbol2, timedelta(days=duration_days), direction2,
                         None, None, None, None, tag)
        )
    
    def _generate_pair_signals(self, pair: Dict) -> List:
        """
        基于z-score为单个配对生成信号
        """
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']
        zscore = pair['zscore']
        
        # 构建标签
        tag = f"{symbol1.Value}&{symbol2.Value}|{pair['alpha_mean']:.4f}|{pair['beta_mean']:.4f}|{zscore:.2f}"
        
        # 风险检查 - 极端偏离（已移除，交给风险管理模块处理）
        # if abs(zscore) > self.upper_limit:
        #     return list(self._create_insight_group(
        #         symbol1, symbol2, 
        #         InsightDirection.Flat, InsightDirection.Flat,
        #         self.flat_signal_duration_days, tag
        #     ))
        
        # 建仓信号
        if abs(zscore) > self.entry_threshold:
            # 根据z-score方向确定交易方向
            if zscore > 0:
                direction1, direction2 = InsightDirection.Down, InsightDirection.Up
            else:
                direction1, direction2 = InsightDirection.Up, InsightDirection.Down
                
            return list(self._create_insight_group(
                symbol1, symbol2, direction1, direction2,
                self.entry_signal_duration_days, tag
            ))
        
        # 平仓信号
        if abs(zscore) < self.exit_threshold:
            return list(self._create_insight_group(
                symbol1, symbol2,
                InsightDirection.Flat, InsightDirection.Flat,
                self.flat_signal_duration_days, tag
            ))
        
        return []


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
        
        self.algorithm.Debug("[AlphaModel] 初始化完成")
    
    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        处理证券变更事件
        步骤1: 解析选股结果
        """
        self.is_selection_day = True
        
        # 添加新股票（使用列表推导式）
        self.symbols.extend([
            s.Symbol for s in changes.AddedSecurities 
            if s.Symbol and s.Symbol not in self.symbols
        ])
        
        # 移除旧股票（使用列表推导式）
        self.symbols = [
            s for s in self.symbols 
            if s not in [r.Symbol for r in changes.RemovedSecurities]
        ]
        
    
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