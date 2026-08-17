#!/usr/bin/env python3
"""
三框架预判回测脚本

模拟16:00收盘后的预判逻辑，在历史数据上跑回测。
每天基于当日指数数据生成明日预判，次日验证评分。

用法：
  python3 ~/.hermes/skills/finance/three-framework-analyst/scripts/backtest.py

数据源：
  新浪 A股指数日K线 API（自动拉取）
  单次获取 200 交易日，回测最近 90 天

输出：
  终端：汇总统计 + 月度趋势 + 偏差分析
  JSON：~/.hermes/data/backtest/results.json
"""
import json, subprocess, sys, math
from datetime import datetime

DATA_DIR = f"{__import__('pathlib').Path.home()}/.hermes/data/backtest"

def fetch_kline():
    """拉取上证指数日K线"""
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=200"
    r = subprocess.run(["curl", "-sL", "--connect-timeout", "10", url],
                       capture_output=True, timeout=15, text=True)
    return json.loads(r.stdout)

def predict_next(day):
    """模拟16:00收盘后的预判逻辑"""
    close = float(day['close'])
    high = float(day['high'])
    low = float(day['low'])
    open_p = float(day.get('open', close))
    change_pct = (close - open_p) / open_p * 100
    amp = (high - low) / close * 100
    
    # 方向判断
    if change_pct > 1.5:
        direction = '震荡偏空' if amp > 3 else '偏多延续'
    elif change_pct > 0.5:
        direction = '偏多/震荡'
    elif change_pct > -0.5:
        direction = '窄幅震荡'
    elif change_pct > -1.5:
        direction = '偏空/震荡'
    else:
        direction = '惯性下跌' if amp > 3 else '偏空/弱反弹'
    
    # 区间判断（振幅调整宽窄）
    vol = 1.5 if amp > 4 else (0.6 if amp < 1 else 1.0)
    support = round(low * (1 - 0.008 * vol), 1)
    resistance = round(high * (1 + 0.008 * vol), 1)
    if support > low * 0.99:
        support = round(low * 0.99, 1)
    if resistance < high * 1.01:
        resistance = round(high * 1.01, 1)
    
    return {'direction': direction, 'support': support, 'resistance': resistance,
            'amplitude': round(amp, 2), 'change_pct': round(change_pct, 2)}

def score_prediction(pred, actual):
    """评分：方向分(1/0) + 区间分(0-1)"""
    actual_close = float(actual['close'])
    actual_open = float(actual.get('open', actual_close))
    actual_change = (actual_close - actual_open) / actual_open * 100
    
    pred_is_up = pred['direction'] in ['偏多延续', '偏多/震荡', '弱反弹']
    pred_is_down = pred['direction'] in ['惯性下跌', '偏空/震荡', '震荡偏空']
    pred_is_flat = pred['direction'] == '窄幅震荡'
    
    dir_score = 1.0 if ((pred_is_up and actual_change > 0) or
                         (pred_is_down and actual_change < 0) or
                         (pred_is_flat and abs(actual_change) < 1)) else 0.0
    
    s, r = pred['support'], pred['resistance']
    if s <= actual_close <= r:
        range_score = 1.0
    else:
        dev = ((s - actual_close) if actual_close < s else (actual_close - r)) / (r - s) if r > s else 0.5
        range_score = max(0, 1 - min(dev, 2))
    
    return {'direction_score': dir_score, 'range_score': range_score,
            'overall': round((dir_score + range_score) / 2, 2),
            'actual_change': round(actual_change, 2)}

def main():
    print("📊 三框架预判回测")
    data = fetch_kline()
    print(f"  数据范围: {data[0]['day']} ~ {data[-1]['day']}")
    print(f"  交易日数: {len(data)}")
    
    # 计算每日涨跌幅
    for i in range(1, len(data)):
        data[i]['change_pct'] = (float(data[i]['close']) - float(data[i-1]['close'])) / float(data[i-1]['close']) * 100
    
    # 回测最近 90 天
    results = []
    for i in range(max(1, len(data) - 90), len(data) - 1):
        pred = predict_next(data[i])
        score = score_prediction(pred, data[i+1])
        results.append({'date': data[i]['day'], 'predict_date': data[i+1]['day'],
                        'today_change': round(data[i].get('change_pct', 0), 2),
                        'prediction': pred, 'tomorrow_change': score['actual_change'],
                        'score': score})
    
    total = len(results)
    dir_correct = sum(1 for r in results if r['score']['direction_score'] == 1.0)
    print(f"\n  回测天数: {total}")
    print(f"  方向准确: {dir_correct}/{total} ({dir_correct/total*100:.1f}%)")
    
    monthly = {}
    for r in results:
        mo = r['date'][:7]
        monthly.setdefault(mo, []).append(r)
    
    print("\n=== 月度统计 ===")
    for mo in sorted(monthly.keys()):
        m = monthly[mo]
        md = sum(1 for r in m if r['score']['direction_score'] == 1.0)
        mr = sum(1 for r in m if r['score']['range_score'] >= 0.5)
        avg = sum(r['score']['overall'] for r in m) / len(m)
        print(f"  {mo}: {len(m)}天 方向{md}/{len(m)}({md/len(m)*100:.0f}%)  综合{avg*100:.0f}/100")
    
    up_d = [r for r in results if r['tomorrow_change'] > 0]
    dn_d = [r for r in results if r['tomorrow_change'] < 0]
    bullish = [r for r in results if r['prediction']['direction'] in ['偏多延续', '偏多/震荡', '弱反弹']]
    bearish = [r for r in results if r['prediction']['direction'] in ['惯性下跌', '偏空/震荡', '震荡偏空']]
    
    print(f"\n=== 偏差分析 ===")
    if up_d: print(f"  实际上涨日: {len(up_d)}天  猜对: {sum(1 for r in up_d if r['score']['direction_score']==1.0)}")
    if dn_d: print(f"  实际下跌日: {len(dn_d)}天  猜对: {sum(1 for r in dn_d if r['score']['direction_score']==1.0)}")
    if bullish: print(f"  偏多预判: {len(bullish)}次 正确率: {sum(1 for r in bullish if r['score']['direction_score']==1.0)/len(bullish)*100:.0f}%")
    if bearish: print(f"  偏空预判: {len(bearish)}次 正确率: {sum(1 for r in bearish if r['score']['direction_score']==1.0)/len(bearish)*100:.0f}%")
    
    # 保存结果
    output = {
        'summary': {'date_range': f"{data[max(1,len(data)-90)]['day']}~{data[-2]['day']}",
                     'total': total, 'direction_accuracy': round(dir_correct/total*100, 1)},
        'monthly': {mo: {'days': len(monthly[mo]),
                          'direction': round(sum(1 for r in monthly[mo] if r['score']['direction_score']==1.0)/len(monthly[mo])*100, 1),
                          'overall': round(sum(r['score']['overall'] for r in monthly[mo])/len(monthly[mo])*100, 1)}
                    for mo in sorted(monthly.keys())},
        'results': results[:20],  # 只保存前20条明细
    }
    subprocess.run(["mkdir", "-p", DATA_DIR])
    path = f"{DATA_DIR}/results.json"
    with open(path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存到 {path}")

if __name__ == '__main__':
    main()
