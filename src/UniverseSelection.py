# region imports
from AlgorithmImports import *
import pandas as pd
import itertools
from statsmodels.tsa.stattools import coint
from Selection.FundamentalUniverseSelectionModel import FundamentalUniverseSelectionModel
from collections import defaultdict
# endregion

class MyUniverseSelectionModel(FundamentalUniverseSelectionModel):
    """
    贝叶斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    
    def __init__(self, algorithm):
        """
        初始化选股模型
        """
        super().__init__(True)              # 启用精细筛选
        self.algorithm = algorithm
        self.rebalanceFlag = True           # 初始化时触发一次选股
        self.numOfCandidates = 50           # 每个行业选50只股票
        self.cointegrated_pairs = {}        # 存储协整对信息
        self.lookback_period = 252          # 默认使用一年数据做协整检验
        self.pvalue_threshold = 0.01        # 协整检验p值阈值
        
        # 股票筛选阈值
        self.min_price = 10                  # 最低股价（美元） 
        self.min_market_cap = 1e9            # 最低市值（10亿美元）
        self.min_volume = 10000000            # 最低成交量 （1000万股）
        self.min_ipo_days = 365              # 最小上市天数（365天）
        self.max_pairs = 10
        self.max_symbol_repeats = 1

        # 记录初始化
        algorithm.Debug("[UniverseSelection] 初始化完成")



    def RebalanceUniverse(self):
        """
        重平衡处理程序 - 触发新一轮选股和模型更新
        """
        self.rebalanceFlag = True
        self.algorithm.Debug(f"[UniverseSelection] -- [RebalanceUniverse] {self.algorithm.Time} 触发重平衡")



    def SelectCoarse(self, algorithm, coarse):
        """
        执行粗筛选过程，根据基本市场指标筛选股票资产
        该函数实现对股票基础特征的初步筛选，剔除不符合流动性、价格和基本面
        要求的股票，为后续的精细筛选提供候选池。
        """

        # 如未触发重平衡条件，则保持当前投资组合不变
        if not self.rebalanceFlag:
            return []     

        algorithm.Debug("[UniverseSelection] -- [SelectCoarse] 被调用")  

        # 以下条件共同作用，确保选出高质量、高流动性的股票：
        #   - 必须有基本面数据 (HasFundamentalData)
        #   - 价格必须大于阈值 (避免低价股风险)
        #   - 市值必须大于阈值 (关注中大型公司)
        #   - 日均交易量必须大于阈值 (保证充分流动性)
        filtered = [x for x in coarse if 
                   x.HasFundamentalData and
                   x.Price > self.min_price and 
                   x.MarketCap > self.min_market_cap and
                   x.Volume > self.min_volume]
        
        # 只选择上市时间超过1年的公司，避免IPO后波动风险
        IpoFiltered = [x for x in filtered if x.SecurityReference.IPODate is not None
                      and (algorithm.Time - x.SecurityReference.IPODate).days > self.min_ipo_days]

        # 按Symbol字母顺序排序，确保结果稳定性
        CoarseSelected = [x.Symbol for x in sorted(IpoFiltered, key=lambda x: x.Symbol.Value)]

        return CoarseSelected



    def SelectFine(self, algorithm, fine):
        """
        执行精筛，根据财务指标进一步筛选资产
        """
        # fine 参数不是 Python 的 list，而是一个 OfTypeIterator（可迭代对象）在你调用 if not fine: 时，这种类型在 Python 中不会被认为是空的，即使里面没有元素
        if not [x.Symbol.Value for x in fine]:
            return []
        
        algorithm.Debug("[UniverseSelection] -- [SelectFine] 被调用")
        
        # 按照行业筛选：科技、医疗、能源、必须消费、通信、工业、公用事业
        techCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Technology]
        healthcareCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Healthcare]
        energyCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Energy]
        consumerDefensiveCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.ConsumerDefensive]
        communicationCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.CommunicationServices]
        industrialsCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Industrials]
        utilitiesCandidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == MorningstarSectorCode.Utilities]
        
        techSelected = self.NumOfCandidates(techCandidates)
        healthcareSelected = self.NumOfCandidates(healthcareCandidates)
        energySelected = self.NumOfCandidates(energyCandidates)
        consumerDefensiveSelected = self.NumOfCandidates(consumerDefensiveCandidates)
        communicationSelected = self.NumOfCandidates(communicationCandidates)
        industrialsSelected = self.NumOfCandidates(industrialsCandidates)
        utilitiesSelected = self.NumOfCandidates(utilitiesCandidates)

        sectorSelected = techSelected + healthcareSelected + \
                         energySelected + consumerDefensiveSelected + \
                         communicationSelected + industrialsSelected + utilitiesSelected  
        
        # 将筛选后的股票分组形成潜在的资产对
        potential_pairs = list(itertools.combinations(sectorSelected, 2))
        # 对每个资产对执行协整检验
        cointegrated_pairs_unfiltered = self.CointegrationTestForPairs(potential_pairs)
        # 对协整对进行筛选
        cointegrated_pairs = self.FilterCointegratedPairs(cointegrated_pairs_unfiltered, max_pairs=self.max_pairs, max_symbol_repeats=self.max_symbol_repeats)
        # 存储通过检验的协整对信息
        self.cointegrated_pairs = cointegrated_pairs

        # 返回最终选出的资产列表 - 所有协整对中的资产   
        selected_symbols = set()
        for pair in cointegrated_pairs:
            selected_symbols.add(pair[0])
            selected_symbols.add(pair[1])
        algorithm.Debug(f"[UniverseSelection] -- [SelectFine] 最终筛选出 {len(selected_symbols)} 只股票，形成 {len(cointegrated_pairs)} 个协整对")

        # 重置重平衡标志，触发新一轮选股
        self.rebalanceFlag = False
        
        return list(selected_symbols)
    


    def NumOfCandidates(self, sectorCandidates):
        """         
        根据行业筛选股票, 并按市值排序, 选取前num只股票
        """
        # 按行业分组
        filtered = [x.symbol for x in sorted(sectorCandidates, key=lambda x: x.DollarVolume, reverse=True)]
        if len(filtered) > self.numOfCandidates:
            return filtered[:self.numOfCandidates]
        else:
            return filtered 
    


    def CointegrationTestForSinglePair(self, symbol1, symbol2):
        """
        执行Dickey-Fuller协整检验
        """
        # 获取两只股票的价格数据
        historySymbol1 = self.algorithm.History([symbol1], self.lookback_period, Resolution.Daily)
        historySymbol2 = self.algorithm.History([symbol2], self.lookback_period, Resolution.Daily)
        
        if historySymbol1.empty or historySymbol2.empty or len(historySymbol1.index.levels[1].unique()) < 2:
            return False, 1.0, None
            
        # 提取价格数据并处理缺失值
        try:
            price1 = historySymbol1.loc[symbol1]['close'].fillna(method='ffill')
            price2 = historySymbol2.loc[symbol2]['close'].fillna(method='ffill')
            
            # 确保两个价格序列长度一致且无缺失值
            if len(price1) != len(price2) or price1.isnull().any() or price2.isnull().any():
                return False, 1.0, None
            
            # 相关性太低，资产对无效
            correlation = price1.corr(price2)
            if abs(correlation) < 0.7:
                return False, 1.0, None
                
            # 执行协整检验
            score, pvalue, critical_values = coint(price1, price2)
            
            # 判断是否协整 (p值小于阈值)
            is_cointegrated = pvalue < self.pvalue_threshold
            return is_cointegrated, pvalue, critical_values
            
        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForSinglePair] 协整检验出错: {str(e)}")
            return False, 1.0, None



    def CointegrationTestForPairs(self, pairs):
        """
        为多个潜在资产对执行协整检验
        """
        # 存储协整检验结果
        cointegrated_pairs = {}
        
        for symbol1, symbol2 in pairs:
            is_cointegrated, pvalue, critical_values = self.CointegrationTestForSinglePair(symbol1, symbol2)
            
            if is_cointegrated:
                pair_key = (symbol1, symbol2)
                cointegrated_pairs[pair_key] = {
                    'pvalue': pvalue,
                    'critical_values': critical_values,
                    'timestamp': self.algorithm.Time
                }
                
                self.algorithm.Debug(f"[UniverseSelection] -- [CointegrationTestForPairs] 发现协整对: [{symbol1.Value} - {symbol2.Value}], p值: {pvalue:.4f}")
        
        return cointegrated_pairs
    

    
    def FilterCointegratedPairs(self, cointegrated_pairs: dict, max_pairs: int, max_symbol_repeats: int) -> dict:
        """
        对协整对进行筛选：
        1. 按 p-value 从小到大排序(优先考虑显著性)
        2. 控制协整对总数量(最多 max_pairs)
        3. 限制每只股票最多出现 max_symbol_repeats 次, 避免过度集中
        """
        # Step 1: 按 p-value 从小到大排序
        sorted_pairs = sorted(cointegrated_pairs.items(), key=lambda kv: kv[1]['pvalue'])

        # Step 2: 按 symbol 去重控制
        symbol_count = defaultdict(int)
        filtered_pairs = {}

        for pair, info in sorted_pairs:
            s1, s2 = pair
            if symbol_count[s1] < max_symbol_repeats and symbol_count[s2] < max_symbol_repeats:
                filtered_pairs[pair] = info
                symbol_count[s1] += 1
                symbol_count[s2] += 1
            if len(filtered_pairs) >= max_pairs:
                break

        return filtered_pairs




