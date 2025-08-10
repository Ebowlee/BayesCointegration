# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
# endregion


class CentralPairManager:
    """
    中央配对管理器 - 策略的信息中心
    
    作为配对信息的唯一真相源，负责：
    1. 接收和存储Alpha模型生成的配对清单
    2. 维护当前活跃配对和历史记录
    3. 为其他模块提供配对查询接口
    
    设计原则：
    - 只读查询：其他模块只能查询，不能修改
    - 快照机制：每轮选股完全覆盖当前活跃清单
    - 历史追踪：所有历史配对都追加保存
    - 规范化存储：使用sorted tuple作为pair_key
    """
    
    def __init__(self, algorithm, config=None):
        """
        初始化中央配对管理器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 配置参数（可选）
        """
        self.algorithm = algorithm
        self.config = config or {}
        
        # 核心数据结构
        self.current_active = {}  # {pair_key: pair_info} 当前活跃配对清单
        self.history_log = []     # 历史记录列表（只追加）
        
        # 轮次管理
        self.cycle_count = 0      # 轮次计数器
        self.last_update_time = None  # 最后更新时间
        self.last_cycle_id = None     # 最后轮次ID
        
        # 统计信息
        self.total_pairs_submitted = 0
        self.total_cycles_processed = 0
    
    def submit_modeled_pairs(self, pairs_data: List[Dict]) -> bool:
        """
        接收Alpha模型提交的配对清单（月度选股后调用）
        
        Args:
            pairs_data: 配对列表，每个元素包含：
                - symbol1: str 第一个股票代码（原始顺序）
                - symbol2: str 第二个股票代码（原始顺序）
                - beta: float 回归系数（symbol1对symbol2）
                - alpha: float 截距项（可选）
                - quality_score: float 配对质量分数
        
        Returns:
            bool: 提交是否成功
        """
        try:
            # 生成轮次标识
            self.cycle_count += 1
            cycle_id = f"cycle_{self.cycle_count}"
            cycle_date = self.algorithm.Time.strftime("%Y%m%d")
            
            self.algorithm.Debug(f"[CPM] 开始处理第{self.cycle_count}轮配对提交，日期：{cycle_date}")
            
            # 清空当前活跃清单（覆盖写）
            self.current_active.clear()
            
            # 处理每个配对
            processed_count = 0
            for pair in pairs_data:
                # 验证必要字段
                if 'symbol1' not in pair or 'symbol2' not in pair:
                    self.algorithm.Debug(f"[CPM] 警告：配对数据缺少symbol字段，跳过")
                    continue
                
                # 创建规范化的pair_key（按字母顺序排序）
                symbol1 = pair['symbol1']
                symbol2 = pair['symbol2']
                pair_key = tuple(sorted([symbol1, symbol2]))
                
                # 构建配对信息
                pair_info = {
                    'pair_key': pair_key,
                    'symbol1': symbol1,  # 保留原始顺序
                    'symbol2': symbol2,  # 用于识别beta方向
                    'beta': pair.get('beta', 1.0),
                    'alpha': pair.get('alpha', 0.0),
                    'quality_score': pair.get('quality_score', 0.5),
                    'cycle_id': cycle_id,
                    'cycle_date': cycle_date,
                    'timestamp': self.algorithm.Time
                }
                
                # 存储到当前活跃清单
                self.current_active[pair_key] = pair_info
                processed_count += 1
            
            # 追加到历史记录
            history_entry = {
                'cycle_id': cycle_id,
                'cycle_date': cycle_date,
                'pair_count': processed_count,
                'pairs': list(self.current_active.values()),
                'timestamp': self.algorithm.Time
            }
            self.history_log.append(history_entry)
            
            # 更新统计信息
            self.last_update_time = self.algorithm.Time
            self.last_cycle_id = cycle_id
            self.total_pairs_submitted += processed_count
            self.total_cycles_processed += 1
            
            self.algorithm.Debug(
                f"[CPM] 第{self.cycle_count}轮配对提交完成：" +
                f"处理{processed_count}个配对，累计{self.total_pairs_submitted}个"
            )
            
            return True
            
        except Exception as e:
            self.algorithm.Error(f"[CPM] 提交配对失败：{str(e)}")
            return False