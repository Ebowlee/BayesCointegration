# region imports
from AlgorithmImports import *
from typing import Dict, Tuple, Optional
# endregion


class RiskCalculator:
    """
    风险计算器 - 无状态的风险指标计算
    
    纯计算功能，不存储任何状态：
    1. 计算配对盈亏
    2. 计算单边盈亏
    3. 计算组合风险指标
    
    使用者：
    - RiskManagement: 计算各种风险指标用于决策
    """
    
    def __init__(self, algorithm):
        """
        初始化风险计算器
        
        Args:
            algorithm: 主算法实例
        """
        self.algorithm = algorithm
    
    def calculate_pair_pnl(self, symbol1: Symbol, symbol2: Symbol) -> Dict[str, float]:
        """
        计算配对的盈亏指标
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            Dict包含:
            - total_pnl: 总盈亏金额
            - total_pnl_percent: 总盈亏百分比
            - symbol1_pnl: symbol1的盈亏
            - symbol2_pnl: symbol2的盈亏
            - total_cost: 总成本
        """
        h1 = self.algorithm.Portfolio[symbol1]
        h2 = self.algorithm.Portfolio[symbol2]
        
        # 如果没有持仓，返回零值
        if not h1.Invested or not h2.Invested:
            return {
                'total_pnl': 0.0,
                'total_pnl_percent': 0.0,
                'symbol1_pnl': 0.0,
                'symbol2_pnl': 0.0,
                'total_cost': 0.0
            }
        
        # 计算总成本（绝对值相加）
        total_cost = abs(h1.HoldingsCost) + abs(h2.HoldingsCost)
        
        # 计算各自盈亏
        symbol1_pnl = h1.UnrealizedProfit
        symbol2_pnl = h2.UnrealizedProfit
        total_pnl = symbol1_pnl + symbol2_pnl
        
        # 计算盈亏百分比
        total_pnl_percent = total_pnl / total_cost if total_cost > 0 else 0.0
        
        return {
            'total_pnl': total_pnl,
            'total_pnl_percent': total_pnl_percent,
            'symbol1_pnl': symbol1_pnl,
            'symbol2_pnl': symbol2_pnl,
            'total_cost': total_cost
        }
    
    def calculate_single_drawdown(self, symbol: Symbol) -> float:
        """
        计算单个股票的回撤率
        
        多头：(现价 - 成本价) / 成本价
        空头：(成本价 - 现价) / 成本价
        
        Args:
            symbol: 股票代码
            
        Returns:
            float: 回撤率（负值表示亏损）
        """
        holding = self.algorithm.Portfolio[symbol]
        
        if not holding.Invested or holding.Quantity == 0:
            return 0.0
        
        avg_price = holding.AveragePrice
        current_price = holding.Price
        
        if avg_price == 0:
            return 0.0
        
        # 根据持仓方向计算回撤
        if holding.Quantity > 0:  # 做多
            drawdown = (current_price - avg_price) / avg_price
        else:  # 做空
            drawdown = (avg_price - current_price) / avg_price
        
        return drawdown
    
    def check_pair_integrity(self, symbol1: Symbol, symbol2: Symbol) -> Tuple[str, Optional[str]]:
        """
        检查配对的完整性
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            Tuple[str, Optional[str]]: (状态码, 错误描述)
            状态码:
            - 'normal': 正常配对持仓
            - 'no_position': 都没持仓
            - 'single_side_only': 单边持仓
            - 'same_direction_error': 同向持仓错误
        """
        h1 = self.algorithm.Portfolio[symbol1]
        h2 = self.algorithm.Portfolio[symbol2]
        
        # 都没持仓
        if not h1.Invested and not h2.Invested:
            return 'no_position', None
        
        # 单边持仓
        if h1.Invested and not h2.Invested:
            return 'single_side_only', f"{symbol1.Value}单边持仓"
        if not h1.Invested and h2.Invested:
            return 'single_side_only', f"{symbol2.Value}单边持仓"
        
        # 都有持仓，检查方向
        if h1.Invested and h2.Invested:
            if (h1.Quantity > 0) == (h2.Quantity > 0):
                direction = "做多" if h1.Quantity > 0 else "做空"
                return 'same_direction_error', f"双边同时{direction}"
            else:
                return 'normal', None
        
        return 'normal', None
    
    def calculate_portfolio_metrics(self) -> Dict[str, float]:
        """
        计算组合层面的风险指标
        
        Returns:
            Dict包含:
            - total_value: 总资产价值
            - total_margin_used: 已用保证金
            - cash: 现金余额
            - total_unrealized_pnl: 未实现盈亏
            - total_unrealized_pnl_percent: 未实现盈亏百分比
            - leverage: 实际杠杆率
        """
        portfolio = self.algorithm.Portfolio
        
        # 基础指标
        total_value = portfolio.TotalPortfolioValue
        total_margin_used = portfolio.TotalMarginUsed
        cash = portfolio.Cash
        
        # 计算总未实现盈亏
        total_unrealized_pnl = sum(
            holding.UnrealizedProfit 
            for holding in portfolio.Values 
            if holding.Invested
        )
        
        # 计算总成本
        total_cost = sum(
            abs(holding.HoldingsCost) 
            for holding in portfolio.Values 
            if holding.Invested
        )
        
        # 盈亏百分比
        total_unrealized_pnl_percent = (
            total_unrealized_pnl / total_cost if total_cost > 0 else 0.0
        )
        
        # 实际杠杆率
        leverage = total_margin_used / total_value if total_value > 0 else 0.0
        
        return {
            'total_value': total_value,
            'total_margin_used': total_margin_used,
            'cash': cash,
            'total_unrealized_pnl': total_unrealized_pnl,
            'total_unrealized_pnl_percent': total_unrealized_pnl_percent,
            'leverage': leverage
        }
    
    def calculate_sector_exposure(self) -> Dict[str, float]:
        """
        计算行业暴露度
        
        Returns:
            Dict[str, float]: 各行业的仓位占比
        """
        sector_exposure = {}
        total_value = self.algorithm.Portfolio.TotalPortfolioValue
        
        if total_value <= 0:
            return sector_exposure
        
        for symbol, holding in self.algorithm.Portfolio.items():
            if not holding.Invested:
                continue
            
            # 获取行业信息
            security = self.algorithm.Securities[symbol]
            if hasattr(security, 'Fundamentals') and security.Fundamentals:
                sector = security.Fundamentals.AssetClassification.MorningstarSectorCode
                sector_name = self._get_sector_name(sector)
                
                # 累加行业暴露
                exposure = abs(holding.HoldingsValue) / total_value
                sector_exposure[sector_name] = sector_exposure.get(sector_name, 0) + exposure
        
        return sector_exposure
    
    def _get_sector_name(self, sector_code) -> str:
        """获取行业名称"""
        # 这里需要从配置中获取行业映射
        # 暂时返回代码字符串
        return str(sector_code)