#!/usr/bin/env python3
"""
许继电气(000400) 止跌信号监控脚本
检测到止跌信号时输出提醒信息，否则输出空字符串（静默）
"""
import urllib.request
import json
import sys
from datetime import datetime

CODE = '000400'
MARKET = 0  # 0=sz
NAME = '许继电气'

def get_kline():
    """获取近10日K线数据（后复权）"""
    mkt = 'sz' if MARKET == 0 else 'sh'
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={mkt}{CODE},day,,,10,qfq"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        day_data = data.get('data', {}).get(f'{mkt}{CODE}', {}).get('qfqday', [])
        if not day_data or len(day_data) < 3:
            return None
        return day_data
    except Exception as e:
        print(f"[ERROR] 获取K线失败: {e}", file=sys.stderr)
        return None

def parse_kline(day_data):
    """解析K线数据"""
    candles = []
    for d in day_data:
        if len(d) >= 6:
            candles.append({
                'date': d[0],
                'open': float(d[1]),
                'close': float(d[2]),
                'high': float(d[3]),
                'low': float(d[4]),
                'volume': float(d[5])
            })
    return candles

def detect_bottom_signals(candles):
    """检测止跌信号"""
    if len(candles) < 3:
        return []
    
    signals = []
    latest = candles[-1]
    prev = candles[-2]
    prev2 = candles[-3]
    
    # 计算均线
    closes = [c['close'] for c in candles]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    
    # 信号1: 长下影线（下影线 >= 实体1.5倍）
    body = abs(latest['close'] - latest['open'])
    lower_shadow = latest['open'] - latest['low'] if latest['close'] >= latest['open'] else latest['close'] - latest['low']
    if body > 0 and lower_shadow >= body * 1.5:
        signals.append({
            'type': '长下影线',
            'desc': f"下影线{lower_shadow:.2f}元，实体{body:.2f}元，下方承接有力",
            'severity': 'strong'
        })
    
    # 信号2: 连续2日收盘价抬高（企稳）
    if latest['close'] > prev['close'] and prev['close'] > prev2['close']:
        signals.append({
            'type': '连续企稳',
            'desc': f"连续3日收盘: {prev2['close']:.2f} -> {prev['close']:.2f} -> {latest['close']:.2f}，重心上移",
            'severity': 'medium'
        })
    
    # 信号3: 放量阳线（成交量>前日1.2倍且收盘>开盘）
    if latest['close'] > latest['open'] and latest['volume'] > prev['volume'] * 1.2:
        signals.append({
            'type': '放量阳线',
            'desc': f"成交量{latest['volume']:.0f}，前日{prev['volume']:.0f}，阳线反弹",
            'severity': 'strong'
        })
    
    # 信号4: 站上MA5
    if ma5 and latest['close'] > ma5:
        signals.append({
            'type': '站上MA5',
            'desc': f"现价{latest['close']:.2f} > MA5({ma5:.2f})，短线转强",
            'severity': 'medium'
        })
    
    # 信号5: 连续缩量（量能萎缩至地量）
    if latest['volume'] < prev['volume'] * 0.8 and prev['volume'] < prev2['volume'] * 0.8:
        signals.append({
            'type': '连续缩量',
            'desc': f"连续缩量，抛压减轻，{latest['date']}成交量{latest['volume']:.0f}",
            'severity': 'medium'
        })
    
    return signals

def main():
    now = datetime.now()
    # 非交易时间也检查，但可以加逻辑跳过（这里简化处理）
    
    day_data = get_kline()
    if not day_data:
        print("")
        return 1
    
    candles = parse_kline(day_data)
    signals = detect_bottom_signals(candles)
    
    if not signals:
        # 无信号，静默
        print("")
        return 0
    
    # 有信号，输出提醒
    latest = candles[-1]
    signal_descs = "\n".join([f"• {s['type']}: {s['desc']}" for s in signals])
    
    msg = f"""⚠️ 许继电气(000400) 止跌信号提醒

📊 当前状态：
• 现价：{latest['close']:.2f}元
• 日期：{latest['date']}
• 今日开盘：{latest['open']:.2f} | 最高：{latest['high']:.2f} | 最低：{latest['low']:.2f}

🔔 触发信号：
{signal_descs}

💡 建议操作：
• 当前处于空头排列，仅作为短线反弹信号
• 可在20-22元区间小仓试探（不超过5%）
• 严格止损：跌破20元无条件止损
• 反弹至MA20(22.38元)以上考虑减仓

⏰ 触发时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"""
    
    print(msg)
    return 0

if __name__ == '__main__':
    sys.exit(main())
