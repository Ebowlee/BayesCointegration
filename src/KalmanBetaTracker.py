# region imports
from AlgorithmImports import *
import numpy as np
from typing import Optional
# endregion


class KalmanBetaTracker:
    """
    卡尔曼滤波 β 追踪器 (v8.6.0)

    实时追踪协整参数 β 的变化，用于区分 Z-score 噪声突破和结构性破裂。

    状态空间模型:
        状态方程: β_t = β_{t-1} + w_t,  w_t ~ N(0, Q)
        观测方程: y_t = α + β_t × x_t + v_t,  v_t ~ N(0, R)

    漂移率计算:
        β_drift = |β_t - β_initial| / |β_initial|

    使用场景:
        - 开仓时初始化 (使用 MCMC 后验参数)
        - 每日健康检查时更新
        - 平仓时销毁
    """

    def __init__(self, beta_init: float, beta_std: float,
                 sigma_ols: float, process_noise: float, alpha: float):
        """
        初始化卡尔曼滤波器

        Args:
            beta_init: 初始 β 估计 (MCMC后验均值)
            beta_std: β 标准差 (MCMC后验标准差)
            sigma_ols: OLS残差标准差 (观测噪声来源)
            process_noise: 过程噪声 Q (独立配置)
            alpha: 协整截距 α (常数)
        """
        self.beta = beta_init
        self.P = beta_std ** 2           # 初始协方差
        self.R = sigma_ols ** 2          # 观测噪声方差
        self.Q = process_noise           # 过程噪声方差
        self.alpha = alpha               # 截距 (常数)
        self.beta_initial = beta_init    # 记录初始值用于计算漂移率

    def update(self, price1: float, price2: float) -> Optional[float]:
        """
        卡尔曼滤波更新 β 估计

        Args:
            price1: 股票1价格 (log(P1) 作为 y)
            price2: 股票2价格 (log(P2) 作为 x)

        Returns:
            更新后的 β 估计，价格无效时返回 None
        """
        if price1 <= 0 or price2 <= 0:
            return None

        # 观测值 (对数价格)
        y = np.log(price1)
        x = np.log(price2)

        # 预测步骤 (Predict)
        beta_pred = self.beta
        P_pred = self.P + self.Q

        # 更新步骤 (Update)
        z = y - (self.alpha + beta_pred * x)  # 观测残差
        S = x * x * P_pred + self.R           # 残差方差
        K = P_pred * x / S                    # 卡尔曼增益

        # 状态更新
        self.beta = beta_pred + K * z
        self.P = (1 - K * x) * P_pred

        return self.beta

    def get_drift_ratio(self) -> Optional[float]:
        """
        计算 β 漂移率

        公式: |β_t - β_initial| / |β_initial|

        Returns:
            漂移率 [0, ∞)，β_initial=0 时返回 None
        """
        if self.beta_initial == 0:
            return None
        return abs(self.beta - self.beta_initial) / abs(self.beta_initial)
