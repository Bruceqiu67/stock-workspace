#!/bin/bash
# 机器人板块三股监控脚本 v3

DATA=$(curl -sL -H "Referer: https://finance.sina.com.cn" \
  "https://hq.sinajs.cn/list=sz300124,sz300660,sz002472" 2>/dev/null)

if [ -z "$DATA" ]; then
  echo "ERROR: 行情数据获取失败"
  exit 1
fi

echo "===== 🤖 机器人三股监控 ====="
echo "🕐 $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 保存原始数据到临时文件，避免编码问题
echo "$DATA" > /tmp/sina_raw.txt

python3 << 'PYEOF'
import re

with open('/tmp/sina_raw.txt', 'rb') as f:
    raw = f.read()

# 尝试多种编码
text = None
for enc in ['gbk', 'gb2312', 'utf-8', 'latin-1']:
    try:
        text = raw.decode(enc)
        break
    except:
        continue

if text is None:
    text = raw.decode('latin-1')

stocks = {
    "sz300124": {"name": "汇川技术", "sector": "伺服电机", "buy": 0.0, "stop": 0.0},
    "sz300660": {"name": "江苏雷利", "sector": "微电机", "buy": 0.0, "stop": 0.0},
    "sz002472": {"name": "双环传动", "sector": "减速器", "buy": 0.0, "stop": 0.0},
}

for code, info in stocks.items():
    # 用正则提取引号内的数据
    m = re.search(r'hq_str_' + code + r'="([^"]*)"', text)
    if not m:
        print(f"[{info['name']}] 数据解析失败")
        continue
    
    fields = m.group(1).split(',')
    if len(fields) < 10:
        print(f"[{info['name']}] 字段不足 ({len(fields)})")
        continue
    
    try:
        open_p = float(fields[1]) if fields[1].strip() else 0
        yest = float(fields[2]) if fields[2].strip() else 0
        cur = float(fields[3]) if fields[3].strip() else 0
        high = float(fields[4]) if fields[4].strip() else 0
        low = float(fields[5]) if fields[5].strip() else 0
        amount = float(fields[9]) if fields[9].strip() else 0
    except (ValueError, IndexError) as e:
        print(f"[{info['name']}] 数值解析失败: {e}")
        continue
    
    chg = (cur - yest) / yest * 100 if yest > 0 else 0
    loss = (cur - info['buy']) / info['buy'] * 100
    
    if amount >= 100000000:
        amt_str = f"{amount/100000000:.1f}亿"
    elif amount >= 10000:
        amt_str = f"{amount/10000:.1f}万"
    else:
        amt_str = f"{amount:.0f}"
    
    if cur < info['stop']:
        alarm = "🚨 跌破止损线！"
    elif cur >= info['buy']:
        alarm = "✅ 已回本/盈利"
    elif loss > -3:
        alarm = "🔸 小幅浮亏"
    elif loss > -5:
        alarm = "⚠️ 浮亏3-5%需关注"
    else:
        alarm = "🔴 浮亏超5%警惕！"
    
    chg_str = f"{chg:+.2f}%"
    loss_str = f"{loss:+.2f}%"
    
    print(f"[{info['name']}] ({info['sector']})")
    print(f"  现价: {cur:.2f}  涨跌: {chg_str}  今开: {open_p:.2f}  昨收: {yest:.2f}")
    print(f"  最高: {high:.2f}  最低: {low:.2f}  成交额: {amt_str}")
    print(f"  买入: {info['buy']:.2f}  浮亏: {loss_str}  止损: {info['stop']:.2f}")
    print(f"  状态: {alarm}")
    print()
PYEOF
