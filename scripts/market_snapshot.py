#!/usr/bin/env python3
"""
收盘数据快照采集器 — 交易日 15:05 自动运行 (no_agent cron)
采集指数、热股、板块排行，存为 JSON 供分析时直接读取。
"""
import json, os, statistics, subprocess, sys, urllib.error, urllib.request
from datetime import date, datetime
from pathlib import Path

try:
    from trading_calendar import (
        calendar_summary,
        freshness as calendar_freshness,
        is_trading_day,
        previous_trading_day,
    )
except ImportError:
    calendar_summary = None
    calendar_freshness = None
    is_trading_day = None
    previous_trading_day = None

try:
    from market_data_adapter import parse_tencent_quote_payload
except ImportError:
    parse_tencent_quote_payload = None

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "market_snapshots"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TODAY = date.today().isoformat()
NOW = datetime.now().isoformat(timespec="minutes")

errors = []
indices = {}
hot_stocks = {}


def fetch_tencent(codes, label="data"):
    """调用 qt.gtimg.cn 获取行情，返回 dict {name: {fields}}。

    解析统一走 market_data_adapter，避免四套 split("~") 实现漂移。
    """
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8", url],
            capture_output=True, timeout=15
        )
        raw = result.stdout.decode("gbk", errors="replace")
    except Exception as e:
        errors.append(f"{label}: curl failed - {e}")
        return {}

    if parse_tencent_quote_payload is None:
        errors.append(f"{label}: market_data_adapter 不可用")
        return {}

    parsed = parse_tencent_quote_payload(raw)
    data = {}
    for quote in parsed.values():
        name = quote.get("name")
        if not name:
            continue
        data[name] = {
            "code": quote.get("code"),
            "price": quote.get("price", 0),
            "change_pct": quote.get("change_pct", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "amount_wan": quote.get("amount_wan", 0),
            "turnover_rate": quote.get("turnover", 0),
            "amplitude": quote.get("amplitude", 0),
        }
    return data


# ── 市场宽度 / 情绪指标（I2） ──────────────────────────────────────────────
SINA_BREADTH_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _limit_pct(code, name):
    """按板块与 ST 标记推断涨跌停幅度；无法判断时用主板 10%。"""
    text_code = str(code or "")
    text_name = str(name or "")
    if "ST" in text_name.upper():
        return 5.0
    if text_code.startswith("sh688") or text_code.startswith("sz30"):
        return 20.0
    return 10.0


def fetch_sina_breadth(max_pages=60, page_size=80, timeout=15):
    """
    拉取 Sina 全 A 股列表并计算宽度指标。

    - 分页上限 60 页 × 80 = 4800 只，覆盖当前 A 股数量。
    - 任一页失败时保留已取得样本，并在返回中标记 `partial=true`。
    - 完全失败返回 status=missing，调用方不得用热股代理冒充全市场宽度。
    """
    rows = []
    pages_fetched = 0
    partial = False
    for page in range(1, max_pages + 1):
        url = (
            f"{SINA_BREADTH_URL}?page={page}&num={page_size}&sort=symbol&asc=1"
            "&node=hs_a&symbol=&_s_r_a=page"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, ValueError, OSError, TimeoutError) as exc:
            errors.append(f"breadth page {page}: {exc}")
            partial = True
            break
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        pages_fetched += 1
        if len(payload) < page_size:
            break

    breadth = compute_breadth(rows, source="sina_market_center")
    breadth["pages_fetched"] = pages_fetched
    breadth["partial"] = partial
    return breadth


def compute_breadth(rows, as_of=None, source="unknown"):
    """
    由全 A 股行情行计算宽度与情绪分（0-100）。

    指标：涨跌家数、涨跌比、涨停/跌停/炸板、涨跌幅中位数、总成交额。
    情绪分权重：上涨占比 35 + 中位数涨幅 20 + 涨跌停平衡 20 + 炸板率 15
    + 成交额分档 10。数据不足时返回 status=missing/degraded，不伪造。
    """
    if not isinstance(rows, list) or not rows:
        return {
            "status": "missing",
            "reason": "无全市场行情样本",
            "as_of": as_of,
            "source": source,
        }

    up = down = flat = 0
    limit_up = limit_down = failed_limit_up = 0
    changes = []
    total_amount = 0.0
    valid = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        change = _to_float(row.get("changepercent"))
        if change is None:
            continue
        valid += 1
        changes.append(change)
        if change > 0:
            up += 1
        elif change < 0:
            down += 1
        else:
            flat += 1

        amount = _to_float(row.get("amount"))
        if amount is not None and amount > 0:
            total_amount += amount

        code = row.get("symbol") or row.get("code") or ""
        name = row.get("name") or ""
        close = _to_float(row.get("trade"))
        prev_close = _to_float(row.get("settlement"))
        high = _to_float(row.get("high"))
        if close is None or prev_close is None or prev_close <= 0:
            continue
        limit = _limit_pct(code, name)
        upper = prev_close * (1 + limit / 100.0)
        lower = prev_close * (1 - limit / 100.0)
        if close >= upper * 0.995:
            limit_up += 1
        elif close <= lower * 1.005:
            limit_down += 1
        elif high is not None and high >= upper * 0.995 and close < upper * 0.995:
            # 盘中触及涨停但收盘未封住，作为炸板代理。
            failed_limit_up += 1

    if valid == 0:
        return {
            "status": "missing",
            "reason": "行情样本缺少有效涨跌幅",
            "as_of": as_of,
            "source": source,
        }

    advance_ratio = up / valid
    median_change = statistics.median(changes)
    attempted_limit_up = limit_up + failed_limit_up
    failed_limit_up_rate = (
        failed_limit_up / attempted_limit_up if attempted_limit_up else 0.0
    )

    # 情绪分：每个分项都给出可解释的归一化值。
    advance_score = max(0.0, min(1.0, advance_ratio)) * 35.0
    median_score = max(0.0, min(1.0, (median_change + 3.0) / 6.0)) * 20.0
    balance_score = max(0.0, min(1.0, (limit_up + 1) / (limit_up + limit_down + 2))) * 20.0
    failed_score = max(0.0, min(1.0, 1.0 - failed_limit_up_rate)) * 15.0
    amount_yi = total_amount / 1e8
    if amount_yi >= 12000:
        amount_score = 10.0
    elif amount_yi >= 9000:
        amount_score = 8.0
    elif amount_yi >= 6000:
        amount_score = 5.0
    else:
        amount_score = 2.0
    sentiment_score = round(
        advance_score + median_score + balance_score + failed_score + amount_score,
        1,
    )
    if sentiment_score >= 80:
        sentiment_tier = "过热"
    elif sentiment_score >= 60:
        sentiment_tier = "偏暖"
    elif sentiment_score >= 40:
        sentiment_tier = "中性"
    elif sentiment_score >= 20:
        sentiment_tier = "偏冷"
    else:
        sentiment_tier = "冰点"

    status = "ok" if valid >= 1000 else "degraded"
    return {
        "status": status,
        "as_of": as_of,
        "source": source,
        "sample_size": valid,
        "advance_count": up,
        "decline_count": down,
        "flat_count": flat,
        "advance_ratio": round(advance_ratio, 4),
        "median_change_pct": round(median_change, 3),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "failed_limit_up": failed_limit_up,
        "failed_limit_up_rate": round(failed_limit_up_rate, 4),
        "total_amount_yi": round(amount_yi, 1),
        "sentiment_score": sentiment_score,
        "sentiment_tier": sentiment_tier,
        "sentiment_breakdown": {
            "advance": round(advance_score, 1),
            "median": round(median_score, 1),
            "limit_balance": round(balance_score, 1),
            "failed_limit": round(failed_score, 1),
            "amount": round(amount_score, 1),
        },
        "formula_version": "1.0",
    }


def main():
    # ----- 1. 指数数据 -----
    idx_codes = [
        "sh000001",    # 上证
        "sz399001",    # 深证
        "sz399006",    # 创业板
        "sh000688",    # 科创50
        "sh000300",    # 沪深300
        "sz399303",    # 国证2000
        "sz399852",    # 中证1000
    ]
    indices.update(fetch_tencent(idx_codes, "indices"))

    # ----- 2. 热股情绪代理 -----
    hot_codes = [
        "sz002371",    # 北方华创 - 半导体设备
        "sh688981",    # 中芯国际 - 晶圆代工
        "sz300502",    # 新易盛 - 光模块
        "sz002050",    # 三花智控 - 机器人
        "sz002472",    # 双环传动 - 减速器
        "sh600276",    # 恒瑞医药 - 创新药
        "sz000977",    # 浪潮信息 - AI服务器
        "sz300059",    # 东方财富 - 证券
    ]
    hot_stocks.update(fetch_tencent(hot_codes, "hot_stocks"))

    # ----- 3. 成交量合计（热股代理，仅作情绪辅助） -----
    total_amount = sum(
        s.get("amount_wan", 0) for s in hot_stocks.values()
    )

    # ----- 4. 全市场宽度/情绪（I2：不再用热股代理代替全市场） -----
    breadth = fetch_sina_breadth()

    # ----- 5. 构建输出 -----
    today_is_trading = is_trading_day(TODAY) if is_trading_day else True
    as_of_day = (
        TODAY if today_is_trading
        else (previous_trading_day(TODAY) if previous_trading_day else TODAY)
    )
    if isinstance(breadth, dict):
        breadth.setdefault("as_of", as_of_day)
    snapshot = {
        "date": as_of_day,
        "captured_at": NOW,
        "as_of": as_of_day,
        "target_date": TODAY,
        "trading_day": today_is_trading,
        "indices": indices,
        "hot_stocks": hot_stocks,
        "hot_total_amount_wan": total_amount,
        "breadth": breadth,
        "freshness": (
            calendar_freshness(as_of_day, TODAY, allow_previous=True)
            if calendar_freshness else {"status": "unknown"}
        ),
        "trading_calendar": (
            calendar_summary(TODAY) if calendar_summary else {"status": "unknown"}
        ),
        "errors": errors if errors else None,
    }

    # 写入文件
    out_path = DATA_DIR / f"{TODAY}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 输出摘要（no_agent cron 模式下 stdout 会推送给用户）
    sh = indices.get("上证指数", {})
    sz = indices.get("深证成指", {})
    cy = indices.get("创业板指", {})
    kc = indices.get("科创50", {})

    print(f"📊 收盘快照 {TODAY}（数据日 {as_of_day}）")
    print(f"   上证: {sh.get('price','-'):>8} ({sh.get('change_pct',0):+.2f}%)")
    print(f"   深成: {sz.get('price','-'):>8} ({sz.get('change_pct',0):+.2f}%)")
    print(f"   创业板: {cy.get('price','-'):>6} ({cy.get('change_pct',0):+.2f}%)")
    print(f"   科创50: {kc.get('price','-'):>6} ({kc.get('change_pct',0):+.2f}%)")
    print(f"   热股成交额合计: {total_amount/10000:.1f}亿")
    if breadth.get("status") in ("ok", "degraded"):
        print(f"   市场宽度: 涨/跌 {breadth['advance_count']}/{breadth['decline_count']} "
              f"(上涨占比 {breadth['advance_ratio']*100:.1f}%, "
              f"涨停 {breadth['limit_up']}, 跌停 {breadth['limit_down']}, "
              f"炸板率 {breadth['failed_limit_up_rate']*100:.0f}%)")
        print(f"   情绪分: {breadth['sentiment_score']}/100 ({breadth['sentiment_tier']}) | "
              f"中位涨幅 {breadth['median_change_pct']:+.2f}% | "
              f"总成交 {breadth['total_amount_yi']:.0f}亿")
    else:
        print(f"   ⚠️  市场宽度缺失: {breadth.get('reason', breadth.get('status'))}", file=sys.stderr)

    # 领涨/领跌热股
    sorted_stocks = sorted(hot_stocks.items(), key=lambda x: x[1].get("change_pct", 0))
    if sorted_stocks:
        worst = sorted_stocks[0]
        best = sorted_stocks[-1]
        print(f"   领涨: {best[0]} {best[1].get('change_pct',0):+.2f}%")
        print(f"   领跌: {worst[0]} {worst[1].get('change_pct',0):+.2f}%")

    if errors:
        print(f"   ⚠️  {len(errors)} 个采集错误", file=sys.stderr)

    # 退出码：有错误但数据不为空 → 0（成功但有瑕疵），完全空 → 1
    if not indices and not hot_stocks:
        print("   ❌ 全部数据采集失败", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
