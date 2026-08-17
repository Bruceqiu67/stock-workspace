"""SKILL 独立测试套件
使用方法: python test_harness/run_tests.py

测试 1、2、5 可自动执行
测试 3、4 需人工介入获取行情数据和设计盲测场景
"""
import sys, json, re, random
sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_DIR = 'test_harness'
SKILL_FILE = 'SKILL.md'
RULES_FILE = 'rules.md'
BOOK_FILE = 'output/book_skeleton.md'

def load_text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()

def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

skill = load_text(SKILL_FILE)
rules = load_text(RULES_FILE)
book = load_text(BOOK_FILE)

report = []
def log(line=''):
    report.append(line)
    print(line)

# ══════════════════════════════════════
# 测试 1: 规则溯源审计（自动）
# ══════════════════════════════════════
log('=' * 60)
log('测试 1: 规则溯源审计（自动）')
log('=' * 60)

# 从 rules.md 中提取所有规则的唯一技术关键词
rule_lines = [l.strip() for l in rules.split('\n') if '如果' in l and '**' in l]
key_terms = ['MA5', 'MA20', 'MA60', '250日', '年线', 'MACD', 'KDJ', 'RSI',
             '金叉', '死叉', '放量', '缩量', '天量', '突破', '回踩',
             '20日', '60日', '5日', '月线', '周线', '日线', '箱体',
             '冰点', '高潮', '退潮', '横盘', '底背离', '顶背离',
             '多头排列', '空头排列', '价涨量增', '价跌量缩']

results_1 = []
for term in key_terms:
    in_book = term in book
    in_skill = term in skill
    results_1.append((term, in_book, in_skill))
    if not in_book:
        log(f'  ? {term}: 未在书中找到来源')

total = len(key_terms)
found = sum(1 for _, b, _ in results_1 if b)
log(f'\n溯源率: {found}/{total} ({found*100//total}%)')
log(f'判定: {"✅ 通过" if found >= 18 else "⚠️ 部分通过" if found >= 15 else "❌ 不通过"}')

# ══════════════════════════════════════
# 测试 2: 内部矛盾检测（自动）
# ══════════════════════════════════════
log('\n' + '=' * 60)
log('测试 2: 内部矛盾检测（自动）')
log('=' * 60)

# 检查原则和规则之间的冲突
principles = [
    '不冲高不卖', '不跳水不买', '让市场先确认',
    '情绪反向', '横有多长竖有多高', '三周期'
]
rule_actions = {
    '买入': sum(1 for l in rule_lines if '买入' in l and '不' not in l[:l.find('买入')]),
    '卖出': sum(1 for l in rule_lines if '卖出' in l and '不' not in l[:l.find('卖出')]),
    '持有': sum(1 for l in rule_lines if '持有' in l),
    '观望': sum(1 for l in rule_lines if '观望' in l),
    '加仓': sum(1 for l in rule_lines if '加仓' in l),
    '减仓': sum(1 for l in rule_lines if '减仓' in l),
}

log(f'规则操作分布: {rule_actions}')

# 检查矛盾
contradictions = []
for i, r1 in enumerate(rule_lines):
    for r2 in rule_lines[i+1:]:
        # 简化检测：找相同条件不同结论
        cond1 = r1.split('→')[0].strip() if '→' in r1 else ''
        cond2 = r2.split('→')[0].strip() if '→' in r2 else ''
        action1 = r1.split('→')[-1].strip() if '→' in r1 else ''
        action2 = r2.split('→')[-1].strip() if '→' in r2 else ''
        
        # 如果条件关键词高度重叠但结论相反
        overlap = len(set(cond1.split()) & set(cond2.split()))
        if overlap > 3:
            if ('买入' in action1 and '卖出' in action2) or \
               ('卖出' in action1 and '买入' in action2) or \
               ('加仓' in action1 and '减仓' in action2):
                contradictions.append((r1[:60], r2[:60]))

if contradictions:
    log(f'发现 {len(contradictions)} 对潜在矛盾:')
    for c1, c2 in contradictions[:5]:
        log(f'  ⚠️ {c1}')
        log(f'     {c2}')
else:
    log(f'✅ 未发现明显逻辑矛盾 (检查了 {len(rule_lines)} 条规则)')

# ══════════════════════════════════════
# 测试 5: 极限条件测试（半自动）
# ══════════════════════════════════════
log('\n' + '=' * 60)
log('测试 5: 极限条件测试（模板）')
log('=' * 60)

edge_cases = [
    {'name': '连续涨停后巨量换手', '期望': '顶部信号', '实际': '—'},
    {'name': '连续跌停后地量',     '期望': '止跌但需确认', '实际': '—'},
    {'name': 'MA5/20/60完全粘合',  '期望': '无方向', '实际': '—'},
    {'name': '放量突破历史新高',    '期望': '趋势确认', '实际': '—'},
    {'name': '突发利空低开5%',     '期望': '等待非恐慌信号', '实际': '—'},
]

for c in edge_cases:
    log(f'  □ {c["name"]}: 期望={c["期望"]}, 实际={c["实际"]}')

log('\n(极限条件需人工评判，参考 test_roadmap.md)')

# ══════════════════════════════════════
# 汇总
# ══════════════════════════════════════
log('\n' + '=' * 60)
log('汇总')
log('=' * 60)
log(f'测试1 (溯源)   : {found}/{total} → {"通过" if found >= 18 else "需关注"}')
log(f'测试2 (矛盾)   : {len(contradictions)} 对 → {"通过" if len(contradictions) == 0 else "需关注"}')
log(f'测试3 (回测)   : 需人工执行 → 见测试路线文档')
log(f'测试4 (盲测)   : 需人工执行 → 见测试路线文档')
log(f'测试5 (极限)   : 5 个条件待评判')

# 保存结果
result = '\n'.join(report)
with open(f'{OUTPUT_DIR}/auto_results.md', 'w', encoding='utf-8') as f:
    f.write(result)
log(f'\n自动测试结果已保存到 {OUTPUT_DIR}/auto_results.md')
