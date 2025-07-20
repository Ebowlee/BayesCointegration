# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
import numpy as np
# endregion

class MyUniverseSelectionModel(FineFundamentalUniverseSelectionModel):
    """
    贝异斯协整选股模型 - 负责选取具有协整关系的资产对
    """
    def __init__(self, algorithm, config=None):
        self.algorithm = algorithm
        self.selection_on = False 
        
        # 默认配置参数
        default_config = {
            'selection': {
                'num_candidates': 30,
                'min_price': 15,
                'min_volume': 2.5e8,
                'min_ipo_days': 1095
            },
            'financial': {
                'max_pe': 30,
                'min_roe': 0.05,
                'max_debt_to_assets': 0.6,
                'max_leverage_ratio': 5
            },
            'volatility': {
                'max_volatility': 0.6,
                'enabled': True
            }
        }
        
        # 合并用户配置
        self.config = self._merge_config(default_config, config or {})
        
        # 参数验证
        self._validate_config()

        self.last_fine_selected_symbols = []                            
        self.fine_selection_count = 0                                   

        algorithm.Debug(f"[UniverseSelection] 初始化完成 - 配置: {self._config_summary()}")

        # 初始化父类并传递自定义的粗选和精选方法
        super().__init__(self._select_coarse, self._select_fine)

    def _merge_config(self, default, user_config):
        """递归合并配置字典"""
        result = default.copy()
        for key, value in user_config.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result
    
    def _validate_config(self):
        """验证配置参数有效性"""
        selection = self.config['selection']
        financial = self.config['financial']
        volatility = self.config['volatility']
        
        # 选股参数验证
        assert selection['num_candidates'] > 0, "num_candidates must be positive"
        assert selection['min_price'] > 0, "min_price must be positive"
        assert selection['min_volume'] > 0, "min_volume must be positive"
        assert selection['min_ipo_days'] >= 0, "min_ipo_days must be non-negative"
        
        # 财务参数验证
        assert financial['max_pe'] > 0, "max_pe must be positive"
        assert 0 < financial['min_roe'] < 1, "min_roe must be between 0 and 1"
        assert 0 < financial['max_debt_to_assets'] < 1, "max_debt_to_assets must be between 0 and 1"
        assert financial['max_leverage_ratio'] > 0, "max_leverage_ratio must be positive"
        
        # 波动率参数验证
        if volatility['enabled']:
            assert volatility['max_volatility'] > 0, "max_volatility must be positive"
    
    def _config_summary(self):
        """生成配置摘要用于日志"""
        s = self.config['selection']
        f = self.config['financial']
        v = self.config['volatility']
        return f"选股{s['num_candidates']}只|价格>${s['min_price']}|PE<{f['max_pe']}|波动率{'开启' if v['enabled'] else '关闭'}"

    def TriggerSelection(self):
        """
        外部调用的触发接口：用于在 main.py 中通过 Schedule.On 控制
        """
        self.selection_on = True
        self.algorithm.Debug("[UniverseSelection] 收到主控调度，准备执行下一轮选股")



    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段，负责从所有股票中筛选出符合条件的股票, 轻量级的筛选方法
        """
        selection_config = self.config['selection']
        
        # 基础筛选
        filtered = [x for x in coarse if x.HasFundamentalData and 
                   x.Price > selection_config['min_price'] and 
                   x.DollarVolume > selection_config['min_volume']]
        
        # IPO时间筛选
        ipo_filtered = [x for x in filtered if x.SecurityReference.IPODate is not None and 
                       (self.algorithm.Time - x.SecurityReference.IPODate).days > selection_config['min_ipo_days']]
        
        # 波动率筛选（如果启用）
        if self.config['volatility']['enabled']:
            volatility_filtered = []
            volatility_failed = 0
            for x in ipo_filtered:
                if self._check_volatility_from_coarse(x):
                    volatility_filtered.append(x)
                else:
                    volatility_failed += 1
            coarse_selected = [x.Symbol for x in volatility_filtered]
            if volatility_failed > 0:
                self.algorithm.Debug(f"[UniverseSelection] 波动率筛选: 通过{len(volatility_filtered)}只, 过滤{volatility_failed}只")
        else:
            coarse_selected = [x.Symbol for x in ipo_filtered]
        
        self.algorithm.Debug(f"[UniverseSelection] 粗选完成: {len(coarse_selected)}只股票通过筛选")
        return coarse_selected

    def _check_volatility_from_coarse(self, coarse_data):
        """从CoarseFundamental数据检查波动率"""
        try:
            max_vol = self.config['volatility']['max_volatility']
            
            # 方法1：检查是否有Volatility属性
            if hasattr(coarse_data, 'Volatility') and coarse_data.Volatility is not None:
                return coarse_data.Volatility <= max_vol
            
            # 方法2：检查PriceVariance
            elif hasattr(coarse_data, 'PriceVariance') and coarse_data.PriceVariance is not None:
                volatility = np.sqrt(coarse_data.PriceVariance * 252)  # 年化
                return volatility <= max_vol
            
            # 如果没有波动率数据，暂时通过
            return True
            
        except Exception as e:
            self.algorithm.Debug(f"[UniverseSelection] 波动率检查失败: {str(e)}")
            return True  # 出错时暂时通过

    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        细选阶段，主要是依据行业和财报信息
        """
        if self.selection_on:
            self.selection_on = False 
            self.fine_selection_count += 1
            self.algorithm.Debug(f"=====================================第【{self.fine_selection_count}】次选股=====================================") 

            sector_candidates = {}
            all_sectors_selected_fine = []

            # 定义 MorningstarSectorCode 到名字的映射
            sector_map = {
                "Technology": MorningstarSectorCode.Technology,
                "Healthcare": MorningstarSectorCode.Healthcare,
                "Energy": MorningstarSectorCode.Energy,
                "ConsumerDefensive": MorningstarSectorCode.ConsumerDefensive,
                "ConsumerCyclical": MorningstarSectorCode.ConsumerCyclical,
                "CommunicationServices": MorningstarSectorCode.CommunicationServices,
                "Industrials": MorningstarSectorCode.Industrials,
                "Utilities": MorningstarSectorCode.Utilities
            }

            # 用循环筛选每个行业
            for name, code in sector_map.items():
                candidates = [x for x in fine if x.AssetClassification.MorningstarSectorCode == code]
                sector_candidates[name] = self._num_of_candidates(candidates)

            # 把每个行业的股票合并到一起
            for selected_fine_list in sector_candidates.values():
                all_sectors_selected_fine.extend(selected_fine_list)

            fine_after_financial_filters = []
            financial_config = self.config['financial']
            financial_failed = 0
            
            for x in all_sectors_selected_fine:
                try:
                    if x.ValuationRatios is not None and x.OperationRatios is not None:
                        pe_ratio = x.ValuationRatios.PERatio
                        roe_ratio = x.OperationRatios.ROE.Value
                        debt_to_assets_ratio = x.OperationRatios.DebtToAssets.Value
                        leverage_ratio = x.OperationRatios.FinancialLeverage.Value

                        passes_pe = pe_ratio is not None and pe_ratio < financial_config['max_pe']
                        passes_roe = roe_ratio is not None and roe_ratio > financial_config['min_roe']
                        passes_debt_to_assets = debt_to_assets_ratio is not None and debt_to_assets_ratio < financial_config['max_debt_to_assets']
                        passes_leverage_ratio = leverage_ratio is not None and leverage_ratio < financial_config['max_leverage_ratio']
                        
                        if passes_pe and passes_roe and passes_debt_to_assets and passes_leverage_ratio:
                            fine_after_financial_filters.append(x)
                        else:
                            financial_failed += 1
                    else:
                        financial_failed += 1
                except Exception as e:
                    self.algorithm.Debug(f"[UniverseSelection] 财务筛选错误 {x.Symbol}: {str(e)}")
                    financial_failed += 1

            final_selected_symbols = [x.Symbol for x in fine_after_financial_filters]
            self.last_fine_selected_symbols = final_selected_symbols
            
            # 详细的选股结果日志
            total_candidates = len(all_sectors_selected_fine)
            self.algorithm.Debug(f"[UniverseSelection] 财务筛选: 候选{total_candidates}只, 通过{len(final_selected_symbols)}只, 过滤{financial_failed}只")
            self.algorithm.Debug(f"[UniverseSelection] 选股完成: 共选出{len(final_selected_symbols)}只, 样本: {[x.Value for x in final_selected_symbols[:5]]}")
            return final_selected_symbols
        else:
            return self.last_fine_selected_symbols


    def _num_of_candidates(self, sectorCandidates):
        """
        根据行业筛选出符合条件的股票，按市值降序排列
        """
        num_candidates = self.config['selection']['num_candidates']
        # 返回市值最高的前 num_candidates 个股票
        filtered = [x for x in sectorCandidates if x.MarketCap is not None and x.MarketCap > 0]
        sorted_candidates = sorted(filtered, key=lambda x: x.MarketCap, reverse=True)
        return sorted_candidates[:num_candidates]
 





