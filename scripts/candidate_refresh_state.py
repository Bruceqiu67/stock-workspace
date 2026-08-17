#!/usr/bin/env python3
"""候选池刷新状态文件 — 记录上次刷新时间"""
import json, datetime
from pathlib import Path

STATE_FILE = Path.home() / '.hermes/data/candidates/refresh_state.json'

def check_needs_refresh():
    """如果超过28天未刷新，返回True"""
    if not STATE_FILE.exists():
        return True
    state = json.loads(STATE_FILE.read_text())
    last = datetime.date.fromisoformat(state['last_refresh'])
    days_since = (datetime.date.today() - last).days
    return days_since >= 28

def record_refresh(summary):
    """记录刷新时间"""
    state = {
        'last_refresh': datetime.date.today().isoformat(),
        'summary': summary,
        'updated_at': datetime.datetime.now().isoformat(),
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"✅ 刷新记录已保存: {STATE_FILE}")

def get_last_refresh():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'last_refresh': 'never'}

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'check':
        print(check_needs_refresh() and 'NEEDS_REFRESH' or 'OK')
    elif len(sys.argv) > 1 and sys.argv[1] == 'status':
        state = get_last_refresh()
        print(f"上次刷新: {state['last_refresh']}")
    else:
        print(__doc__)
