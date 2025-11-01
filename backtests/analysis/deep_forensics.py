"""
Deep Forensic Analysis - 回答用户的4个核心问题

核心问题:
1. 开仓滑点: signal_zscore vs entry_zscore
2. 平仓滑点: exit_signal_zscore vs actual_exit_zscore
3. 冷静期机制: 为何反复开仓?
4. 质量分数: 高质量配对为何亏损?

设计原则: 数据驱动,逐配对深度追踪,不做草草总结
"""

import pandas as pd
import re
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path


class LogParser:
    """解析logs.txt提取关键数据"""

    def __init__(self, log_path):
        self.log_path = log_path
        with open(log_path, 'r', encoding='utf-8') as f:
            self.lines = f.readlines()

    def extract_pair_scores(self, pair_tuple):
        """
        提取配对的历次质量分数

        日志格式:
        [PairScore] (CVX , EPD ): Q=0.553 [PASS] | Half=0.000(days=41.9) | BetaStab=0.979(CV=0.103) | MeanRev=0.953(SNR_κ=3.20) | Resid=0.464(RRS=1.081)
        """
        sym1, sym2 = pair_tuple
        scores = []

        for line in self.lines:
            if '[PairScore]' in line and sym1 in line and sym2 in line:
                # 提取时间戳
                timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                timestamp = timestamp_match.group(1) if timestamp_match else None

                # 提取各项指标
                q_match = re.search(r'Q=([\d.]+)', line)
                half_match = re.search(r'days=([\d.]+)', line)
                cv_match = re.search(r'CV=([\d.]+)', line)
                snr_match = re.search(r'SNR_κ=([\d.]+)', line)
                rrs_match = re.search(r'RRS=([\d.]+)', line)

                if q_match:
                    scores.append({
                        'timestamp': timestamp,
                        'quality_score': float(q_match.group(1)),
                        'half_life_days': float(half_match.group(1)) if half_match else None,
                        'beta_cv': float(cv_match.group(1)) if cv_match else None,
                        'snr_kappa': float(snr_match.group(1)) if snr_match else None,
                        'rrs': float(rrs_match.group(1)) if rrs_match else None
                    })

        return scores

    def extract_entry_signals(self, pair_tuple):
        """
        提取开仓信号时刻的zscore

        日志格式:
        [候选筛选] ('ET', 'FTI'): signal=SHORT_SPREAD, zscore=1.270, quality=0.761
        """
        sym1, sym2 = pair_tuple
        signals = []

        for line in self.lines:
            if '[候选筛选]' in line and f"('{sym1}', '{sym2}')" in line:
                # 提取时间戳
                timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                timestamp = timestamp_match.group(1) if timestamp_match else None

                # 提取信号和zscore
                signal_match = re.search(r'signal=([A-Z_]+)', line)
                zscore_match = re.search(r'zscore=([-\d.]+)', line)

                if signal_match and signal_match.group(1) in ['SHORT_SPREAD', 'LONG_SPREAD']:
                    signals.append({
                        'timestamp': timestamp,
                        'signal': signal_match.group(1),
                        'zscore': float(zscore_match.group(1)) if zscore_match else None
                    })

        return signals

    def extract_close_signals(self, pair_tuple):
        """
        提取平仓信号(从持仓配对检查日志)

        需要找到类似: [持仓检查] ('ET', 'FTI'): signal=CLOSE, zscore=0.15
        """
        sym1, sym2 = pair_tuple
        signals = []

        for line in self.lines:
            # 搜索包含配对和CLOSE信号的行
            if f"('{sym1}', '{sym2}')" in line and 'signal=CLOSE' in line:
                timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                timestamp = timestamp_match.group(1) if timestamp_match else None

                zscore_match = re.search(r'zscore=([-\d.]+)', line)

                signals.append({
                    'timestamp': timestamp,
                    'signal': 'CLOSE',
                    'zscore': float(zscore_match.group(1)) if zscore_match else None
                })

        return signals


class TradeReconstructor:
    """从CSV重建完整交易历史"""

    def __init__(self, csv_path):
        self.df = pd.read_csv(csv_path)
        self.df['Time'] = pd.to_datetime(self.df['Time'])
        self._parse_tags()

    def _parse_tags(self):
        """解析Tag列提取配对和操作信息"""
        def parse_tag(tag):
            parts = tag.split('_')
            pair = parts[0].replace("('", "").replace("')", "").replace("', '", ",")
            action = parts[1]
            reason = parts[2] if len(parts) > 2 else 'N/A'
            return pair, action, reason

        self.df['Pair'], self.df['Action'], self.df['Reason'] = zip(*self.df['Tag'].apply(parse_tag))

    def build_trade_timeline(self, pair_name):
        """构建配对的完整交易时间线"""
        pair_df = self.df[self.df['Pair'] == pair_name].sort_values('Time')

        trades = []
        current_position = None

        for idx, row in pair_df.iterrows():
            action = row['Action']

            if action == 'OPEN':
                # 开仓记录
                symbol1, symbol2 = pair_name.split(',')

                leg1 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol1)]
                leg2 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol2)]

                if len(leg1) > 0 and len(leg2) > 0:
                    leg1_row = leg1.iloc[0]
                    leg2_row = leg2.iloc[0]

                    current_position = {
                        'open_time': row['Time'],
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'open_price1': leg1_row['Price'],
                        'open_price2': leg2_row['Price'],
                        'open_qty1': leg1_row['Quantity'],
                        'open_qty2': leg2_row['Quantity'],
                        'open_value1': leg1_row['Value'],
                        'open_value2': leg2_row['Value'],
                    }

            elif action == 'CLOSE' and current_position:
                # 平仓记录
                symbol1 = current_position['symbol1']
                symbol2 = current_position['symbol2']

                leg1 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol1)]
                leg2 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol2)]

                if len(leg1) > 0 and len(leg2) > 0:
                    leg1_row = leg1.iloc[0]
                    leg2_row = leg2.iloc[0]

                    current_position.update({
                        'close_time': row['Time'],
                        'close_reason': row['Reason'],
                        'close_price1': leg1_row['Price'],
                        'close_price2': leg2_row['Price'],
                        'close_qty1': leg1_row['Quantity'],
                        'close_qty2': leg2_row['Quantity'],
                        'close_value1': leg1_row['Value'],
                        'close_value2': leg2_row['Value'],
                    })

                    # 计算PnL和持仓天数
                    pnl = (current_position['close_value1'] + current_position['close_value2']) + \
                          (current_position['open_value1'] + current_position['open_value2'])
                    holding_days = (current_position['close_time'] - current_position['open_time']).days

                    current_position['pnl'] = pnl
                    current_position['holding_days'] = holding_days

                    trades.append(current_position)
                    current_position = None

        return trades

    def identify_worst_pairs(self, top_n=5):
        """识别亏损最严重的配对"""
        pair_pnl = defaultdict(float)

        for pair in self.df['Pair'].unique():
            pair_df = self.df[self.df['Pair'] == pair]
            total_value = pair_df['Value'].sum()
            pair_pnl[pair] = total_value

        # 按PnL排序(从小到大,负值最前)
        sorted_pairs = sorted(pair_pnl.items(), key=lambda x: x[1])
        return sorted_pairs[:top_n]


class DeepForensics:
    """主分析器 - 逐配对深度追踪"""

    def __init__(self, csv_path, log_path):
        self.log_parser = LogParser(log_path)
        self.trade_reconstructor = TradeReconstructor(csv_path)

    def analyze_pair(self, pair_name):
        """分析单个配对的完整生命周期"""
        print("\n" + "=" * 100)
        print(f"配对: {pair_name}")
        print("=" * 100)

        # 1. 重建交易历史
        trades = self.trade_reconstructor.build_trade_timeline(pair_name)

        if not trades:
            print("[WARNING] 未找到交易记录")
            return

        # 2. 提取日志数据
        pair_tuple = tuple(pair_name.split(','))
        pair_scores = self.log_parser.extract_pair_scores(pair_tuple)
        entry_signals = self.log_parser.extract_entry_signals(pair_tuple)
        close_signals = self.log_parser.extract_close_signals(pair_tuple)

        # 3. 计算累计PnL
        total_pnl = sum(t['pnl'] for t in trades)
        print(f"\n总交易次数: {len(trades)}")
        print(f"累计PnL: ${total_pnl:,.2f}\n")

        # 4. 逐笔分析
        for i, trade in enumerate(trades, 1):
            self._analyze_single_trade(i, trade, pair_scores, entry_signals, close_signals)

        # 5. 冷静期检查
        if len(trades) > 1:
            self._check_cooldown_violations(trades)

    def _analyze_single_trade(self, trade_num, trade, pair_scores, entry_signals, close_signals):
        """分析单笔交易"""
        print(f"\n{'-' * 100}")
        print(f"[交易{trade_num}] {trade['open_time'].date()} 至 {trade['close_time'].date()}")
        print(f"{'-' * 100}")

        # 开仓信息
        print(f"\n[开仓] {trade['open_time']}")

        # 匹配对应的入场信号
        entry_signal = self._find_closest_signal(entry_signals, trade['open_time'])
        if entry_signal:
            print(f"  信号zscore: {entry_signal['zscore']:.3f} (信号={entry_signal['signal']})")

        # 匹配质量分数
        score = self._find_closest_score(pair_scores, trade['open_time'])
        if score:
            print(f"\n[质量分数]")
            print(f"  quality_score: {score['quality_score']:.3f}")
            print(f"  half_life: {score['half_life_days']:.1f}天")
            print(f"  beta_cv: {score['beta_cv']:.3f}")
            print(f"  SNR_κ: {score['snr_kappa']:.2f}")
            print(f"  RRS: {score['rrs']:.3f}")

        print(f"\n[持仓过程]")
        price_change1 = (trade['close_price1'] - trade['open_price1']) / trade['open_price1'] * 100
        price_change2 = (trade['close_price2'] - trade['open_price2']) / trade['open_price2'] * 100
        print(f"  {trade['symbol1']}: ${trade['open_price1']:.2f} -> ${trade['close_price1']:.2f} ({price_change1:+.2f}%)")
        print(f"  {trade['symbol2']}: ${trade['open_price2']:.2f} -> ${trade['close_price2']:.2f} ({price_change2:+.2f}%)")
        print(f"  持仓天数: {trade['holding_days']}天")

        # 平仓信息
        print(f"\n[平仓] {trade['close_time']} (原因={trade['close_reason']})")

        # PnL
        print(f"\n[PnL] ${trade['pnl']:+,.2f}")

        # 失败分析
        if trade['pnl'] < 0:
            self._diagnose_failure(trade, score, price_change1, price_change2)

    def _find_closest_signal(self, signals, target_time):
        """找到最接近目标时间的信号"""
        if not signals:
            return None

        # 移除目标时间的时区信息以便比较
        if hasattr(target_time, 'tz'):
            target_time = target_time.tz_localize(None)

        # 简化: 返回最近的信号
        for signal in signals:
            if signal['timestamp']:
                signal_time = datetime.strptime(signal['timestamp'], '%Y-%m-%d %H:%M:%S')
                # 如果信号在目标时间前24小时内
                if abs((signal_time - target_time).total_seconds()) < 86400:
                    return signal

        return None

    def _find_closest_score(self, scores, target_time):
        """找到最接近目标时间的质量分数"""
        if not scores:
            return None

        # 移除目标时间的时区信息以便比较
        if hasattr(target_time, 'tz'):
            target_time = target_time.tz_localize(None)

        for score in scores:
            if score['timestamp']:
                score_time = datetime.strptime(score['timestamp'], '%Y-%m-%d %H:%M:%S')
                # 如果分数在目标时间前7天内
                if abs((score_time - target_time).total_seconds()) < 7 * 86400:
                    return score

        return None

    def _diagnose_failure(self, trade, score, price_change1, price_change2):
        """诊断交易失败原因"""
        print(f"\n[失败诊断]")

        # Beta对冲失败检测
        if abs(price_change1) > 2 and abs(price_change2) > 2:
            if (price_change1 > 0 and price_change2 > 0) or (price_change1 < 0 and price_change2 < 0):
                print(f"  [FAIL] Beta对冲失败: 两腿同向变动 ({price_change1:+.2f}% vs {price_change2:+.2f}%)")

        # 质量评分盲点
        if score and score['quality_score'] > 0.7:
            print(f"  [WARNING] 高质量配对亏损: quality_score={score['quality_score']:.3f}")

            if score['snr_kappa'] > 2.5:
                print(f"    - SNR_κ={score['snr_kappa']:.2f}显示均值回归显著,但实际未回归")

            if score['beta_cv'] < 0.2:
                print(f"    - beta_cv={score['beta_cv']:.3f}显示Beta稳定,但可能忽略了时变性")

    def _check_cooldown_violations(self, trades):
        """检查冷静期违规"""
        print(f"\n{'-' * 100}")
        print("[冷静期检查]")
        print(f"{'-' * 100}")

        for i in range(len(trades) - 1):
            current_trade = trades[i]
            next_trade = trades[i + 1]

            # 计算间隔
            cooldown_days = (next_trade['open_time'] - current_trade['close_time']).days

            # 根据平仓原因判断预期冷静期
            reason = current_trade['close_reason']
            if 'STOP' in reason:
                expected_cooldown = 60
            elif reason == 'CLOSE':
                expected_cooldown = 20
            else:
                expected_cooldown = 30

            print(f"\n交易{i+1} -> 交易{i+2}:")
            print(f"  实际间隔: {cooldown_days}天")
            print(f"  预期冷静期: {expected_cooldown}天 (基于{reason})")

            if cooldown_days < expected_cooldown:
                print(f"  [FAIL] 违反冷静期! 过早重新开仓")
            else:
                print(f"  [PASS] 满足冷静期要求")


def main():
    # 文件路径
    base_path = Path(r"c:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests")
    csv_path = base_path / "Sleepy Orange Dinosaur_trades.csv"
    log_path = base_path / "Sleepy Orange Dinosaur_logs.txt"

    print("=" * 100)
    print("DEEP FORENSIC ANALYSIS - Sleepy Orange Dinosaur")
    print("=" * 100)
    print(f"\n数据源:")
    print(f"  CSV: {csv_path.name}")
    print(f"  LOG: {log_path.name}")

    # 初始化分析器
    forensics = DeepForensics(str(csv_path), str(log_path))

    # 识别TOP 5亏损配对
    print(f"\n{'-' * 100}")
    print("识别亏损最严重的配对...")
    print(f"{'-' * 100}")

    worst_pairs = forensics.trade_reconstructor.identify_worst_pairs(top_n=5)

    for pair, pnl in worst_pairs:
        print(f"  {pair:20s}: ${pnl:12.2f}")

    # 对每个配对执行深度追踪
    for pair, pnl in worst_pairs:
        forensics.analyze_pair(pair)

    print("\n" + "=" * 100)
    print("分析完成")
    print("=" * 100)


if __name__ == '__main__':
    main()
