# region imports
from AlgorithmImports import *
from src.Pairs import Pairs
# endregion


class PairsFactory:
    """Pairs对象工厂 - 负责从建模结果创建Pairs对象"""

    def __init__(self, algorithm, config: dict):
        """
        初始化工厂
        Args:
            algorithm: QCAlgorithm实例
            config: 配置字典（alpha_model部分）
        """
        self.algorithm = algorithm
        self.config = config

    def create_pairs(self, modeled_pairs):
        """
        从建模结果创建Pairs对象字典

        Args:
            modeled_pairs: 贝叶斯建模结果列表

        Returns:
            dict: {pair_id: Pairs对象}
        """
        pairs_dict = {}

        for model_result in modeled_pairs:
            # 准备Pairs构造函数需要的数据
            pair_data = self._prepare_pair_data(model_result)

            # 创建Pairs对象
            pair = Pairs(pair_data, self.config)

            # 存储到字典中
            pairs_dict[pair.pair_id] = pair

            self.algorithm.Debug(f"  创建配对: {pair.pair_id}, 质量分数: {pair_data['quality_score']:.3f}")

        return pairs_dict

    def _prepare_pair_data(self, model_result):
        """
        准备Pairs构造函数需要的数据
        将贝叶斯模型输出转换为Pairs需要的格式
        """
        # 注意: 贝叶斯模型输出的是residual统计，在协整关系中residual就是spread
        return {
            'symbol1': model_result['symbol1'],
            'symbol2': model_result['symbol2'],
            'sector': model_result['sector'],
            'alpha_mean': model_result['alpha_mean'],
            'beta_mean': model_result['beta_mean'],
            'spread_mean': model_result['residual_mean'],  # residual即为spread
            'spread_std': model_result['residual_std'],     # residual_std即为spread_std
            'quality_score': model_result['quality_score']
        }