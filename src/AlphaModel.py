# region imports
from AlgorithmImports import *
from datetime import timedelta
import itertools
from collections import defaultdict
from statsmodels.tsa.stattools import coint
import numpy as np
import pymc as pm
# endregion

class DataQualityManager:
    """
    数据质量管理器 - 一次性完整的数据质量检查
    
    在数据加载后进行一次性完整检查，后续使用完全信任数据质量
    """
    
    def __init__(self, algorithm, lookback_period, min_data_completeness_ratio=0.98):
        self.algorithm = algorithm
        self.lookback_period = lookback_period
        self.min_data_completeness_ratio = min_data_completeness_ratio
        
        # 有效symbol集合
        self.valid_symbols = set()
    
    def validate_symbols_quality(self, symbols, historical_data_cache):
        """
        一次性完整的数据质量检查
        
        检查项目：
        1. 数据存在性（有close列）
        2. 数据长度（==252天，98%完整性）  
        3. 价格合理性（正数，无空值）
        
        Args:
            symbols: 待验证的股票列表
            historical_data_cache: 历史数据缓存
            
        Returns:
            dict: 包含valid_symbols和invalid_symbols的字典
        """
        # 重置有效symbol集合
        self.valid_symbols.clear()
        
        valid_symbols = set()
        invalid_symbols = set()
        
        for symbol in symbols:
            try:
                # 1. 数据存在性检查
                if symbol not in historical_data_cache:
                    invalid_symbols.add(symbol)
                    continue
                
                symbol_data = historical_data_cache[symbol]
                if symbol_data.empty or 'close' not in symbol_data.columns:
                    invalid_symbols.add(symbol)
                    continue
                
                close_prices = symbol_data['close']
                
                # 2. 数据长度和完整性检查
                required_length = int(self.lookback_period * self.min_data_completeness_ratio)
                if len(close_prices) < required_length:
                    invalid_symbols.add(symbol)
                    continue
                
                # 3. 价格合理性检查
                # 对少量空值进行填充（因为已经满足98%完整性要求）
                if close_prices.isnull().any():
                    close_prices = close_prices.fillna(method='pad').fillna(method='bfill')
                    # 更新缓存中的数据
                    historical_data_cache[symbol]['close'] = close_prices
                
                # 检查价格为正数（在填充后检查）
                if not (close_prices > 0).all():
                    invalid_symbols.add(symbol)
                    continue
                
                # 通过所有检查
                valid_symbols.add(symbol)
                
            except Exception as e:
                self.algorithm.Debug(f"[DataQualityManager] 数据检查异常 {symbol.Value}: {str(e)[:50]}")
                invalid_symbols.add(symbol)
        
        # 更新内部状态
        self.valid_symbols = valid_symbols
        
        # 输出统计信息
        self.algorithm.Debug(
            f"[DataQualityManager] 数据质量检查: {len(symbols)}只 → "
            f"有效{len(valid_symbols)}只, 无效{len(invalid_symbols)}只"
        )
        
        return {
            'valid_symbols': valid_symbols,
            'invalid_symbols': invalid_symbols,
            'total': len(symbols),
            'valid_count': len(valid_symbols),
            'invalid_count': len(invalid_symbols)
        }
    
    def is_valid_symbol(self, symbol):
        """检查symbol是否有效（已通过所有质量检查）"""
        return symbol in self.valid_symbols


class CointegrationTestManager:
    """
    协整检验管理器 - 专门处理协整检验相关逻辑
    
    负责协整检验的执行、筛选和管理，简化主模型的复杂度
    """
    
    def __init__(self, algorithm, config, data_quality_manager, historical_data_cache):
        self.algorithm = algorithm
        self.data_quality_manager = data_quality_manager
        self.historical_data_cache = historical_data_cache
        
        # 协整检验相关配置
        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']
    
    def perform_sector_cointegration_tests(self, sector_to_symbols):
        """
        执行行业内协整检验
        
        Args:
            sector_to_symbols: 行业到股票列表的映射
            
        Returns:
            tuple: (industry_cointegrated_pairs, sector_pairs_info)
        """
        industry_cointegrated_pairs = {}
        sector_pairs_info = {}
        
        for sector, symbols in sector_to_symbols.items():
            # 每个行业内进行组合配对, 逐个进行协整检验
            pairs = list(itertools.combinations(symbols, 2))
            intra_industry_pairs = {}

            for pair_tuple in pairs:
                symbol1, symbol2 = pair_tuple
                cointegrated_pair = self._test_single_pair(symbol1, symbol2)
                intra_industry_pairs.update(cointegrated_pair)
        
            filtered_intra_industry_pairs = self._filter_cointegrated_pairs(intra_industry_pairs)
            
            # 记录该行业的协整对信息
            if filtered_intra_industry_pairs:
                pair_names = [f"{s1.Value}-{s2.Value}" for (s1, s2) in filtered_intra_industry_pairs.keys()]
                sector_pairs_info[sector] = {
                    'count': len(filtered_intra_industry_pairs),
                    'pairs': pair_names
                }
            
            industry_cointegrated_pairs.update(filtered_intra_industry_pairs)
        
        return industry_cointegrated_pairs, sector_pairs_info
    
    def _test_single_pair(self, symbol1, symbol2):
        """
        对单个股票对执行协整检验
        
        Args:
            symbol1, symbol2: 待检验的股票对
            
        Returns:
            dict: 协整对信息字典
        """
        cointegrated_pair = {}
        
        try:
            # 直接获取预处理的价格数据，无需检查数据质量
            price1 = self.historical_data_cache[symbol1]['close']
            price2 = self.historical_data_cache[symbol2]['close']
            
            # 3. 相关性预筛选
            correlation = price1.corr(price2)
            if abs(correlation) < self.correlation_threshold:
                return cointegrated_pair
            
            # 4. Engle-Granger协整检验
            score, pvalue, critical_values = coint(price1, price2)
            is_cointegrated = pvalue < self.pvalue_threshold

            if is_cointegrated:
                pair_key = (symbol1, symbol2)
                cointegrated_pair[pair_key] = {
                    'pvalue': pvalue,
                    'critical_values': critical_values,
                    'create_time': self.algorithm.Time
                }
                
        except (KeyError, ValueError, ImportError):
            return {}
        except Exception as e:
            self.algorithm.Debug(f"[CointegrationTest] 检验异常 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")

        return cointegrated_pair
    
    def _filter_cointegrated_pairs(self, cointegrated_pairs):
        """
        按质量筛选协整对, 控制投资组合规模
        
        Args:
            cointegrated_pairs: 原始协整对字典
            
        Returns:
            dict: 筛选后的协整对字典
        """
        # 按p值排序, 选择最强协整关系的股票对
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])
        symbol_count = defaultdict(int)
        filtered_pairs = {}
        
        for keys, values in sorted_pairs:
            s1, s2 = keys
            # 避免单一股票在多个配对中重复使用
            if symbol_count[s1] <= self.max_symbol_repeats and symbol_count[s2] <= self.max_symbol_repeats:
                filtered_pairs[keys] = values
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) >= self.max_pairs:
                break
                
        return filtered_pairs


class BayesianModelingManager:
    """
    贝叶斯建模管理器 - 统一管理所有贝叶斯建模相关逻辑
    
    负责历史后验管理、建模策略决策、数据准备和PyMC建模执行
    """
    
    def __init__(self, algorithm, config, data_quality_manager, historical_data_cache):
        self.algorithm = algorithm
        self.data_quality_manager = data_quality_manager
        self.historical_data_cache = historical_data_cache
        
        # 配置参数
        self.lookback_period = config['lookback_period']
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        self.dynamic_update_enabled = config.get('dynamic_update_enabled', True)
        self.min_data_completeness_ratio = config.get('min_data_completeness_ratio', 0.98)
        self.prior_coverage_ratio = config.get('prior_coverage_ratio', 0.95)
        self.min_mcmc_samples = config.get('min_mcmc_samples', 500)
        
        # 历史后验存储
        self.historical_posteriors = {}
    
    def get_prior_params(self, pair_key):
        """
        获取协整对的历史后验参数
        
        检查正向和反向键，返回找到的历史后验参数
        """
        if not self.dynamic_update_enabled:
            return None
            
        # 检查正向键
        if pair_key in self.historical_posteriors:
            return self.historical_posteriors[pair_key]
        
        # 检查反向键
        symbol1, symbol2 = pair_key
        reverse_key = (symbol2, symbol1)
        if reverse_key in self.historical_posteriors:
            return self.historical_posteriors[reverse_key]
        
        return None
    
    def determine_lookback_days(self, prior_params):
        """
        根据历史后验决定回看天数
        
        如果有历史后验且间隔合理，使用间隔天数
        否则使用完整回看期（252天）
        """
        if prior_params is None:
            return self.lookback_period
        
        current_time = self.algorithm.Time
        last_posterior_time = prior_params.get('update_time', current_time)
        interval_days = (current_time - last_posterior_time).days
        
        # 如果间隔超过1年，使用完整回看期
        if interval_days > 252:
            return self.lookback_period
        
        # 使用实际间隔天数
        return interval_days
    
    def prepare_modeling_data(self, symbol1, symbol2, lookback_days):
        """
        统一的建模数据准备方法
        
        symbol都已通过数据质量检查，直接使用缓存数据
        返回：(x_data, y_data)
        """
        # 直接提取价格数据，无需检查数据质量
        price1 = self.historical_data_cache[symbol1]['close'].tail(lookback_days)
        price2 = self.historical_data_cache[symbol2]['close'].tail(lookback_days)
        
        # 转换为对数形式
        y_data = np.log(price1.values)
        x_data = np.log(price2.values)
        
        return x_data, y_data
    
    def _extract_posterior_statistics(self, posterior):
        """
        统一的后验统计计算方法
        
        集中处理所有后验参数的统计计算，避免重复代码
        
        Args:
            posterior: PyMC后验对象
            
        Returns:
            dict: 包含所有统计信息的字典
        """
        # 提取原始样本
        alpha_samples = posterior['alpha'].values.flatten()
        beta_samples = posterior['beta'].values.flatten()
        sigma_samples = posterior['sigmaOfEpsilon'].values.flatten()
        residuals_samples = posterior['residuals'].values.flatten()
        
        # 计算统计量
        return {
            # 原始样本（用于历史后验保存）
            'alpha_samples': alpha_samples,
            'beta_samples': beta_samples,
            'sigma_samples': sigma_samples,
            'residuals_samples': residuals_samples,
            
            # 统计量（用于信号生成）
            'alpha': alpha_samples,
            'alpha_mean': float(alpha_samples.mean()),
            'beta': beta_samples,
            'beta_mean': float(beta_samples.mean()),
            'residuals': residuals_samples,
            'residuals_mean': float(residuals_samples.mean()),
            'residuals_std': float(residuals_samples.std())
        }
    
    def _process_posterior_results(self, posterior, pair_key):
        """
        整合的后验处理方法
        
        一次性完成后验参数提取和历史保存，消除功能重复
        
        Args:
            posterior: PyMC后验对象
            pair_key: 协整对键值
            
        Returns:
            dict: 处理后的后验参数
        """
        # 统一提取后验统计
        posterior_stats = self._extract_posterior_statistics(posterior)
        
        # 保存历史后验（用于下次动态更新）
        self.historical_posteriors[pair_key] = {
            'alpha_samples': posterior_stats['alpha_samples'],
            'beta_samples': posterior_stats['beta_samples'],
            'sigma_samples': posterior_stats['sigma_samples'],
            'update_time': self.algorithm.Time
        }
        
        return posterior_stats
    
    def _determine_sampling_config(self, prior_params):
        """
        采样配置决策方法
        
        基于prior_params是否存在来决定采样策略，而非lookback_days
        
        Args:
            prior_params: 历史后验参数
            
        Returns:
            tuple: (draws, tune, is_dynamic_update)
        """
        if prior_params is not None:
            # 动态更新：使用轻量级采样
            draws = max(self.mcmc_posterior_samples // 2, self.min_mcmc_samples)
            tune = max(self.mcmc_warmup_samples // 2, self.min_mcmc_samples)
            is_dynamic_update = True
        else:
            # 完整建模：使用标准采样
            draws = self.mcmc_posterior_samples
            tune = self.mcmc_warmup_samples
            is_dynamic_update = False
        
        return draws, tune, is_dynamic_update
    
    def perform_single_pair_modeling(self, symbol1, symbol2, lookback_days, prior_params=None):
        """
        简化的PyMC贝叶斯建模方法
        
        Args:
            symbol1, symbol2: 协整对的两个股票
            lookback_days: 回看天数（由调用方决定）
            prior_params: 历史后验参数（可选）
        
        Returns:
            posterior_params: 建模成功的后验参数，失败返回None
        """
        try:
            # 准备建模数据，无需检查数据质量
            data = self.prepare_modeling_data(symbol1, symbol2, lookback_days)
            if data is None:
                return None
            
            x_data, y_data = data
            
            # 2. 配置先验分布参数
            if prior_params is not None:
                # 验证先验参数
                if (len(prior_params.get('alpha_samples', [])) == 0 or 
                    len(prior_params.get('beta_samples', [])) == 0 or
                    len(prior_params.get('sigma_samples', [])) == 0):
                    return None
                
                # 使用历史后验作为先验
                alpha_mu = float(np.mean(prior_params['alpha_samples']))
                alpha_sigma = max(float(np.std(prior_params['alpha_samples'])), 0.1)
                beta_mu = float(np.mean(prior_params['beta_samples']))
                beta_sigma = max(float(np.std(prior_params['beta_samples'])), 0.1)
                
                # 改进的sigma先验设置:使用可配置的覆盖比例
                sigma_mean = float(np.mean(prior_params['sigma_samples']))
                sigma_std = float(np.std(prior_params['sigma_samples']))
                coverage_multiplier = 2 if self.prior_coverage_ratio >= 0.95 else 1.5
                sigma_sigma = max(sigma_mean + coverage_multiplier*sigma_std, 0.1)
            else:
                # 使用无信息先验
                alpha_mu, alpha_sigma = 0, 10
                beta_mu, beta_sigma = 0, 10
                sigma_sigma = 1
            
            # 3. 构建PyMC模型并执行采样
            with pm.Model() as model:
                # 先验分布
                alpha = pm.Normal('alpha', mu=alpha_mu, sigma=alpha_sigma)
                beta = pm.Normal('beta', mu=beta_mu, sigma=beta_sigma)
                sigmaOfEpsilon = pm.HalfNormal('sigmaOfEpsilon', sigma=sigma_sigma)
                
                # 线性关系
                mu = alpha + beta * x_data
                
                # 似然函数
                likelihood = pm.Normal('y', mu=mu, sigma=sigmaOfEpsilon, observed=y_data)
                
                # 残差计算
                residuals = pm.Deterministic('residuals', y_data - mu)
                
                # 决定采样配置(基于prior_params存在性)
                draws, tune, is_dynamic_update = self._determine_sampling_config(prior_params)
                
                trace = pm.sample(draws=draws, tune=tune, 
                                chains=self.mcmc_chains, cores=1, progressbar=False)
                
            
            # 4. 整合后验处理(提取+保存)
            pair_key = (symbol1, symbol2)
            posterior_params = self._process_posterior_results(trace.posterior, pair_key)
            
            return posterior_params
                
        except ImportError as e:
            self.algorithm.Debug(f"[BayesianManager] PyMC导入错误: {str(e)[:50]}")
            return None
        except (ValueError, RuntimeError) as e:
            return None
        except Exception as e:
            operation_type = "动态更新" if prior_params is not None else "完整建模"
            self.algorithm.Debug(f"[BayesianManager] {operation_type}失败 {symbol1.Value}-{symbol2.Value}: {str(e)[:50]}")
            return None
    
    def perform_all_pairs_modeling(self, cointegrated_pairs):
        """
        对所有协整对执行贝叶斯建模
        
        返回包含后验参数和统计信息的字典
        """
        posterior_params = {}
        statistics = {
            'success_count': 0,
            'dynamic_update_count': 0,
            'new_modeling_count': 0,
            'dynamic_update_pairs': [],
            'new_modeling_pairs': []
        }
        
        for pair_key, pair_info in cointegrated_pairs.items():
            symbol1, symbol2 = pair_key
            
            # 1. 获取历史后验（如果有）
            prior_params = self.get_prior_params(pair_key)
            
            # 2. 决定回看天数
            lookback_days = self.determine_lookback_days(prior_params)
            
            # 3. 执行建模(建模策略由perform_single_pair_modeling内部决定)
            posterior_param = self.perform_single_pair_modeling(
                symbol1, symbol2,
                lookback_days=lookback_days,
                prior_params=prior_params
            )
            
            # 4. 更新结果和统计
            if posterior_param is not None:
                posterior_params[pair_key] = posterior_param
                statistics['success_count'] += 1
                
                # 判断建模类型(与内部逻辑保持一致)
                if prior_params is not None:
                    statistics['dynamic_update_count'] += 1
                    statistics['dynamic_update_pairs'].append(f"{symbol1.Value}-{symbol2.Value}")
                else:
                    statistics['new_modeling_count'] += 1
                    statistics['new_modeling_pairs'].append(f"{symbol1.Value}-{symbol2.Value}")
        
        return {
            'posterior_params': posterior_params,
            'statistics': statistics
        }


class BayesianCointegrationAlphaModel(AlphaModel):
    """
    贝叶斯协整Alpha模型
    
    该模型使用贝叶斯方法和MCMC采样技术构建配对交易策略, 主要功能包括:
    1. 通过Engle-Granger检验识别行业内协整股票对
    2. 使用PyMC进行贝叶斯线性回归, 生成alpha和beta的后验分布
    3. 基于残差z-score判断价格偏离程度, 生成均值回归交易信号
    4. 实现波动率筛选和风险控制, 确保交易对的质量
    
    核心原理: 协整对的残差应平稳且均值为零, 当价格偏离超过阈值时产生交易机会
    """
    
    # =========================
    # 1. 初始化和框架接口方法
    # =========================
    
    def __init__(self, algorithm, config, pair_ledger, sector_code_to_name):
        """
        初始化贝叶斯协整Alpha模型
        
        Args:
            algorithm: QuantConnect算法实例, 提供数据和交易接口
            config: 配置字典, 包含所有模型参数和阈值设置
            pair_ledger: 配对记账簿实例
            sector_code_to_name: 行业代码到名称的映射字典
        """
        super().__init__() 
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        self.is_universe_selection_on = False
        self.symbols = []

        # 协整检验配置
        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']
        
        # 贝叶斯建模配置
        self.lookback_period = config['lookback_period']
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        
        # 信号生成配置
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.upper_limit = config['upper_limit']
        self.lower_limit = config['lower_limit']
        self.flat_signal_duration_days = config.get('flat_signal_duration_days', 1)
        self.entry_signal_duration_days = config.get('entry_signal_duration_days', 2)
        
        # 波动率筛选配置
        self.max_annual_volatility = config['max_annual_volatility']
        self.volatility_window_days = config['volatility_window_days']
        
        # 动态更新配置
        self.selection_interval_days = config.get('selection_interval_days', 30)
        self.dynamic_update_enabled = config.get('dynamic_update_enabled', True)
        self.min_beta_threshold = config.get('min_beta_threshold', 0.2)
        self.max_beta_threshold = config.get('max_beta_threshold', 3.0)
        
        # 数据质量配置
        self.min_data_completeness_ratio = config.get('min_data_completeness_ratio', 0.95)
        self.prior_coverage_ratio = config.get('prior_coverage_ratio', 0.95)
        self.min_mcmc_samples = config.get('min_mcmc_samples', 500)
        
        # 历史数据缓存
        self.historical_data_cache = {}
        
        # 数据质量管理器
        self.data_quality_manager = DataQualityManager(algorithm, self.lookback_period, self.min_data_completeness_ratio)
        
        # 协整检验管理器
        self.cointegration_manager = None  # 在数据加载后初始化
        
        # 贝叶斯建模管理器
        self.bayesian_manager = None  # 在数据加载后初始化
        
        # 选股日期跟踪
        self.last_selection_date = None
        
        # 使用从main.py传入的行业映射
        self.sector_code_to_name = sector_code_to_name

        self.algorithm.Debug(f"[AlphaModel] 初始化完成 (最大{self.max_pairs}对, 波动率<{self.max_annual_volatility:.0%})")

    
    def Update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        """
        Alpha模型核心更新方法, 分阶段执行协整检验和信号生成
        
        该方法分为两个清晰的阶段:
        1. 选股日: 进行批量协整检验和贝叶斯建模 (月度执行)
        2. 交易日: 计算z-score并生成交易信号 (每日执行)
        """
        if not self.symbols or len(self.symbols) < 2:
            self.algorithm.Debug("[AlphaModel] 当前未接受到足够数量的选股")
            return []
        
        # 阶段1: 选股日处理
        if self.is_universe_selection_on:
            self._process_selection_day()
            
        # 阶段2: 每日信号生成
        return self._generate_daily_signals(data)

    
    def OnSecuritiesChanged(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        """
        证券变更事件回调, 同步更新内部股票池
        
        当UniverseSelection模块更新股票池时, 框架自动调用此方法.
        设置选股标志位触发下次Update时的协整检验流程.
        """
        self.is_universe_selection_on = True

        for added_security in changes.AddedSecurities:
            symbol = added_security.Symbol
            if symbol and symbol not in self.symbols:
                self.symbols.append(symbol)
    
        for removed_security in changes.RemovedSecurities:
            symbol = removed_security.Symbol
            if symbol and symbol in self.symbols:
                self.symbols.remove(symbol)

    
    # ===========================
    # 2. 选股日处理流程 (按执行顺序)
    # ===========================
    
    def _process_selection_day(self):
        """
        选股日处理: 执行协整检验和贝叶斯建模
        
        主要流程:
        1. 计算选股间隔并更新动态参数
        2. 批量加载历史数据
        3. 波动率筛选
        4. 行业内协整检验
        5. 贝叶斯建模
        6. 更新配对记账簿
        """
        # 1. 计算实际选股间隔
        actual_interval_days = self.selection_interval_days
        if self.last_selection_date is not None:
            actual_interval_days = (self.algorithm.Time - self.last_selection_date).days
            self.algorithm.Debug(f"[AlphaModel] 实际选股间隔: {actual_interval_days}天 (上次: {self.last_selection_date.strftime('%Y-%m-%d')})")
        
        # 初始化选股日状态
        self.industry_cointegrated_pairs = {}
        self.posterior_params = {}
        self.last_selection_date = self.algorithm.Time
        
        # 2. 数据准备阶段
        self._load_and_validate_historical_data(self.symbols)
        
        # 3. 波动率筛选
        volatility_filtered_symbols = self._filter_by_volatility(self.symbols)
        
        # 4. 行业分组
        sector_to_symbols = self._group_symbols_by_industry(volatility_filtered_symbols)
        
        # 5. 初始化协整检验管理器(在数据加载后)
        if self.cointegration_manager is None:
            self.cointegration_manager = CointegrationTestManager(
                self.algorithm, 
                {
                    'pvalue_threshold': self.pvalue_threshold,
                    'correlation_threshold': self.correlation_threshold,
                    'max_symbol_repeats': self.max_symbol_repeats,
                    'max_pairs': self.max_pairs
                },
                self.data_quality_manager,
                self.historical_data_cache
            )
        
        # 6. 初始化贝叶斯建模管理器(在数据加载后)
        if self.bayesian_manager is None:
            self.bayesian_manager = BayesianModelingManager(
                self.algorithm,
                {
                    'lookback_period': self.lookback_period,
                    'mcmc_warmup_samples': self.mcmc_warmup_samples,
                    'mcmc_posterior_samples': self.mcmc_posterior_samples,
                    'mcmc_chains': self.mcmc_chains,
                    'dynamic_update_enabled': self.dynamic_update_enabled,
                    'min_data_completeness_ratio': self.min_data_completeness_ratio,
                    'prior_coverage_ratio': self.prior_coverage_ratio,
                    'min_mcmc_samples': self.min_mcmc_samples
                },
                self.data_quality_manager,
                self.historical_data_cache
            )
        
        # 7. 协整检验阶段
        self._perform_cointegration_tests(sector_to_symbols)
        
        # 8. 贝叶斯建模阶段
        self._perform_bayesian_modeling()
        
        # 9. 更新配对记账簿
        successful_pairs = [(symbol1, symbol2) for (symbol1, symbol2) in self.posterior_params.keys()]
        self.pair_ledger.update_pairs(successful_pairs)
        
        # 重置选股标志
        self.is_universe_selection_on = False

    
    def _load_and_validate_historical_data(self, symbols: list[Symbol]):
        """
        加载历史数据并进行一次性完整质量检查
        """
        try:
            # 一次性获取所有股票的历史数据
            all_histories = self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
            self.historical_data_cache = {}
            
            # 数据预处理: 填充缺失值并建立缓存
            for symbol in symbols:
                if symbol in all_histories.index.get_level_values(0):
                    symbol_data = all_histories.loc[symbol]
                    # 处理缺失值
                    close_prices = symbol_data['close'].fillna(method='pad').fillna(method='bfill')
                    symbol_data['close'] = close_prices
                    self.historical_data_cache[symbol] = symbol_data
            
            # 使用数据质量管理器进行一次性完整验证
            quality_result = self.data_quality_manager.validate_symbols_quality(symbols, self.historical_data_cache)
            
            # 清理包含无效symbol的协整对
            if quality_result['invalid_symbols']:
                self._cleanup_invalid_cointegrated_pairs(quality_result['invalid_symbols'])
                        
        except Exception as e:
            self.algorithm.Debug(f"[AlphaModel] 数据加载异常: {str(e)[:50]}")
            self.historical_data_cache = {}
    
    def _cleanup_invalid_cointegrated_pairs(self, invalid_symbols):
        """
        从协整对集合中移除包含无效symbol的配对
        
        Args:
            invalid_symbols: 数据质量不合格的symbol集合
        """
        if not hasattr(self, 'industry_cointegrated_pairs') or not self.industry_cointegrated_pairs:
            return
        
        pairs_to_remove = []
        for pair_key in self.industry_cointegrated_pairs:
            symbol1, symbol2 = pair_key
            if symbol1 in invalid_symbols or symbol2 in invalid_symbols:
                pairs_to_remove.append(pair_key)
        
        # 执行清理
        for pair_key in pairs_to_remove:
            del self.industry_cointegrated_pairs[pair_key]
        
        if pairs_to_remove:
            self.algorithm.Debug(
                f"[AlphaModel] 协整对清理: 移除{len(pairs_to_remove)}对 "
                f"(包含无效symbol: {len(invalid_symbols)}只)"
            )

    
    def _filter_by_volatility(self, symbols: list[Symbol]) -> list[Symbol]:
        """
        基于年化波动率进行股票筛选
        
        只处理已通过数据质量检查的symbol，无需重复验证数据质量
        """
        filtered_symbols = []
        volatility_failed = 0
        
        # 只处理有效的symbols
        valid_symbols = [s for s in symbols if self.data_quality_manager.is_valid_symbol(s)]
        
        for symbol in valid_symbols:
            # 直接使用缓存数据进行波动率计算，无需检查
            price_data = self.historical_data_cache[symbol]['close']
            recent_data = price_data.tail(self.volatility_window_days)
            returns = recent_data.pct_change().dropna()
            
            daily_volatility = returns.std()
            annual_volatility = daily_volatility * np.sqrt(252)
            
            if annual_volatility <= self.max_annual_volatility:
                filtered_symbols.append(symbol)
            else:
                volatility_failed += 1
        
        # 筛选统计输出
        invalid_count = len(symbols) - len(valid_symbols)
        self.algorithm.Debug(
            f"[AlphaModel] 波动率筛选: 候选{len(valid_symbols)}只 → 通过{len(filtered_symbols)}只 "
            f"(波动率过滤{volatility_failed}只, 数据无效{invalid_count}只)"
        )
            
        return filtered_symbols

    
    def _group_symbols_by_industry(self, symbols: list[Symbol]) -> dict:
        """
        按行业分组股票, 为同行业配对做准备
        
        协整关系在同行业内更为稳定, 跨行业配对容易受到行业轮动影响.
        支持8个主要Morningstar行业分类.
        """
        sector_to_symbols = defaultdict(list)
        
        # 分类symbols到对应行业
        for symbol in symbols:
            security = self.algorithm.Securities[symbol]
            sector = security.Fundamentals.AssetClassification.MorningstarSectorCode
            sector_name = self.sector_code_to_name.get(sector)
            if sector_name:
                sector_to_symbols[sector_name].append(symbol)
        
        return sector_to_symbols

    
    def _perform_cointegration_tests(self, sector_to_symbols):
        """
        执行行业内协整检验
        """
        # 使用协整检验管理器执行检验
        self.industry_cointegrated_pairs, sector_pairs_info = self.cointegration_manager.perform_sector_cointegration_tests(sector_to_symbols)

        # 输出行业协整对汇总
        if sector_pairs_info:
            summary_parts = []
            for sector, info in sector_pairs_info.items():
                summary_parts.append(f"{sector}({info['count']})")
            
            self.algorithm.Debug(f"[AlphaModel] 行业协整对: {' '.join(summary_parts)}")

    
    def _perform_bayesian_modeling(self):
        """
        对所有协整对执行贝叶斯建模
        
        使用BayesianModelingManager统一管理建模过程
        """
        # 执行建模
        results = self.bayesian_manager.perform_all_pairs_modeling(
            self.industry_cointegrated_pairs
        )
        
        # 更新后验参数
        self.posterior_params = results['posterior_params']
        
        # 输出统计信息
        stats = results['statistics']
        self.algorithm.Debug(
            f"[AlphaModel] 建模统计: 动态更新{stats['dynamic_update_count']}对, "
            f"完整建模{stats['new_modeling_count']}对, 总成功{stats['success_count']}对"
        )
        
        # 按建模方式分类显示协整对
        if stats['dynamic_update_pairs']:
            self.algorithm.Debug(f"[AlphaModel] 动态更新: {', '.join(stats['dynamic_update_pairs'])}")
        if stats['new_modeling_pairs']:
            self.algorithm.Debug(f"[AlphaModel] 完整建模: {', '.join(stats['new_modeling_pairs'])}")
    
    
    # ============================
    # 3. 每日信号生成流程
    # ============================
    
    def _generate_daily_signals(self, data: Slice) -> List[Insight]:
        """
        每日信号生成: 基于现有协整对计算z-score并生成交易信号
        """
        insights = []
        
        # 遍历所有成功建模的协整对, 计算当前价格偏离并生成交易信号
        for pair_key in self.posterior_params.keys():
            symbol1, symbol2 = pair_key
            posterior_param = self.posterior_params[pair_key]
            posterior_param_with_zscore = self._calculate_residual_zscore(symbol1, symbol2, data, posterior_param)
            signal = self._generate_signals_for_pair(symbol1, symbol2, posterior_param_with_zscore)
            insights.extend(signal)
        
        return insights

    
    def _calculate_residual_zscore(self, symbol1, symbol2, data: Slice, posteriorParamSet):
        """
        计算当前价格相对于协整关系的偏离程度
        
        使用贝叶斯模型的后验参数计算当前时点的残差, 并标准化为z-score.
        z-score反映价格偏离协整均衡的程度, 是交易信号生成的核心指标.
        """
        if not (data.ContainsKey(symbol1) and data[symbol1] is not None and data.ContainsKey(symbol2) and data[symbol2] is not None):
            return None
        
        current_price1 = np.log(data[symbol1].Close)
        current_price2 = np.log(data[symbol2].Close)
        
        alpha_mean = posteriorParamSet['alpha_mean']
        beta_mean = posteriorParamSet['beta_mean']  
        epsilon_mean = posteriorParamSet['residuals_mean']
        epsilon_std = posteriorParamSet['residuals_std']
        
        # 使用历史均值作基准调整, 避免z-score过大
        residual = current_price1 - (alpha_mean + beta_mean * current_price2) - epsilon_mean

        if epsilon_std != 0:   
            zscore = residual / epsilon_std
        else:
            zscore = 0
        posteriorParamSet['zscore'] = zscore

        return posteriorParamSet

    
    def _generate_signals_for_pair(self, symbol1, symbol2, posteriorParamSet):
        """
        基于z-score生成配对交易信号
        
        根据价格偏离程度生成不同类型的交易信号:
        - 入场信号: z-score超过入场阈值时, 做空高估股票, 做多低估股票
        - 出场信号: z-score回归到退出阈值时, 平仓获利
        - 止损信号: z-score超过风险上限时, 强制平仓止损
        """
        if posteriorParamSet is None:
            return []
        
        z = posteriorParamSet['zscore']
        
        # 根据z分数确定信号类型和方向
        signal_config = self._get_signal_configuration(z)
        if signal_config is None:
            # 观望阶段: 不发射任何信号
            return []
        
        insight1_direction, insight2_direction, trend = signal_config
        
        # 根据信号类型设置不同的有效期
        if insight1_direction == InsightDirection.Flat:
            # 平仓信号
            duration = timedelta(days=self.flat_signal_duration_days)
        else:
            # 建仓信号
            duration = timedelta(days=self.entry_signal_duration_days)
        
        # 直接生成信号,不做任何拦截或验证
        tag = f"{symbol1.Value}&{symbol2.Value}|{posteriorParamSet['alpha_mean']:.4f}|{posteriorParamSet['beta_mean']:.4f}|{z:.2f}|{len(self.industry_cointegrated_pairs)}"
        
        insight1 = Insight.Price(symbol1, duration, insight1_direction, tag=tag)
        insight2 = Insight.Price(symbol2, duration, insight2_direction, tag=tag)
        signals = [insight1, insight2]
        
        return Insight.group(signals)

    
    def _get_signal_configuration(self, z):
        """
        将z-score映射到具体的交易信号配置
        
        信号区间划分:
        - [entry_threshold, upper_limit]: 做空symbol1, 做多symbol2
        - [lower_limit, -entry_threshold]: 做多symbol1, 做空symbol2  
        - (-exit_threshold, exit_threshold): 平仓回归信号
        - 超出upper/lower_limit: 止损平仓
        - 其他区间: 观望等待
        """
        if self.entry_threshold <= z <= self.upper_limit:
            return (InsightDirection.Down, InsightDirection.Up, "卖 | 买")
        elif self.lower_limit <= z <= -self.entry_threshold:
            return (InsightDirection.Up, InsightDirection.Down, "买 | 卖")
        elif -self.exit_threshold < z < self.exit_threshold:
            return (InsightDirection.Flat, InsightDirection.Flat, "回归")
        elif z > self.upper_limit or z < self.lower_limit:
            return (InsightDirection.Flat, InsightDirection.Flat, "失效")
        else:
            return None