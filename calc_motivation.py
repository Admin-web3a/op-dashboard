#!/usr/bin/env python3
"""
W3A Motivation Calculator
=========================
Считает месячные бонусы:
  - Филипп Тимуш (Philip Lorez)  — Entry Offer (Money-Dick + PetRock) + non-paid base
  - Кирилл Осипов                — трафик: CPL + количество регистраций

Запуск:
    python calc_motivation.py                           # текущий месяц
    python calc_motivation.py --month 2026-08           # конкретный месяц
    python calc_motivation.py --month 2026-08 \\
        --kirill-plan-cpl 480 --kirill-actual-cpl 520 \\
        --kirill-plan-regs 313 --kirill-actual-regs 290
"""

import urllib.request
import json
import os
import re
import csv
import io
import argparse
import datetime
from pathlib import Path

# ── .env ───────────────────────────────────────────────────────────────────────

def load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()

# ── Константы ─────────────────────────────────────────────────────────────────

DOMAIN = os.environ.get("AMO_DOMAIN", "simmihur.amocrm.ru")
TOKEN  = os.environ.get("AMO_TOKEN", "")

# Воронки Entry Offer
MD_PIPELINE_ID   = 11095182   # Money-Dick
PR_PIPELINE_ID   = 11218594   # PetRock
DIAG_PIPELINE_ID = 11121294   # Диагностика (для проверки non-paid)

# Статус «Оплачено» в каждой воронке
MD_PAID_STATUS = 87315374
PR_PAID_STATUS = 88019034

# Custom field IDs (UTM)
UTM_SOURCE_FIELD   = 1323539   # UTM Source
UTM_MEDIUM_FIELD   = 1323541   # UTM Medium
UTM_CAMPAIGN_FIELD = 1323543   # UTM Campaign

# UTM-источники, которые считаются non-paid (email/TG/боты)
# Обнови под реальные значения utm_source и utm_medium в вашем проекте
NON_PAID_SOURCES = set(
    os.environ.get("NON_PAID_SOURCES", "email,tg,telegram,bot,tg_channel,organic,tg_post").split(",")
)
NON_PAID_MEDIUMS = set(
    os.environ.get("NON_PAID_MEDIUMS", "email,tg_post,tg_channel,bot,newsletter,organic").split(",")
)

# Google Sheets медиаплан (публичный — auth не нужен)
SHEETS_ID  = "1A6cMBmHVz2-5ctwVeyHy4LMl5vbnucQ4_EGXnIAGmUY"
SHEETS_GID = 0   # лист «Медиаплан»

# Оклады
PHIL_SALARY   = 160_000
KIRILL_SALARY = 150_000

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

# ── Тиры бонусов ──────────────────────────────────────────────────────────────

# Entry Offer: порог месячной выручки → % со всей выручки
EO_TIERS = [
    (5_000_000, 0.01),   # 5 млн+  → 1%
    (1_000_000, 0.02),   # 1 млн+  → 2%
    (0,         0.04),   # до 1 млн → 4%
]

def get_eo_rate(revenue: float) -> float:
    for threshold, rate in EO_TIERS:
        if revenue >= threshold:
            return rate
    return 0.04

def get_non_paid_rate(eo_revenue: float) -> float:
    """Non-paid % привязан к тиру выручки Entry Offer."""
    if eo_revenue >= 5_000_000:
        return 0.02
    if eo_revenue >= 1_000_000:
        return 0.04
    return 0.08

# Кирилл: минимальный % выполнения плана → бонус
KIRILL_TIERS = [
    (1.00, 45_000),
    (0.90, 35_000),
    (0.85, 25_000),
    (0.80, 20_000),
    (0.00,      0),
]

def get_kirill_bonus(pct: float) -> int:
    for threshold, bonus in KIRILL_TIERS:
        if pct >= threshold:
            return bonus
    return 0

# ── amoCRM API ─────────────────────────────────────────────────────────────────

def api_get(path: str) -> dict:
    url = f"https://{DOMAIN}/api/v4/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read()
        return json.loads(body) if body.strip() else {}

def fetch_all_pages(path_template: str, key: str = "leads") -> list:
    """Fetch paginated results from amoCRM."""
    results = []
    page = 1
    while True:
        data = api_get(path_template.format(page=page))
        batch = data.get("_embedded", {}).get(key, [])
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 250:
            break
        page += 1
    return results

MD_LAST_EVENT_FIELD = 1323553   # MD Last Event (текстовое поле в Money-Dick/PetRock)

def fetch_paid_deals(pipeline_id: int, status_id: int,
                     ts_from: int, ts_to: int) -> list:
    """Сделки в статусе «Оплачено», обновлённые в заданном диапазоне,
    с MD Last Event = 'paid' (реально оплатили, не просто передвинули статус)."""
    path = (
        f"leads?filter[pipeline_id][0]={pipeline_id}"
        f"&filter[status_id][0]={status_id}"
        f"&filter[updated_at][from]={ts_from}"
        f"&filter[updated_at][to]={ts_to}"
        f"&with=custom_fields&limit=250&page={{page}}"
    )
    all_leads = fetch_all_pages(path, key="leads")
    # Фильтруем только те, у которых MD Last Event = 'paid'
    confirmed = []
    for lead in all_leads:
        for cf in lead.get("custom_fields_values", []) or []:
            if cf["field_id"] == MD_LAST_EVENT_FIELD:
                vals = [v["value"].lower() for v in cf.get("values", [])]
                if "paid" in vals:
                    confirmed.append(lead)
                    break
    return confirmed

def fetch_deal_notes(lead_id: int) -> list:
    return fetch_all_pages(
        f"leads/{lead_id}/notes?limit=250&page={{page}}", key="notes"
    )

def parse_payment_note(text: str) -> float | None:
    """Парсит сумму только из ноты события 'paid' (содержит transactionId).
    Исключает checkout_intent_created / payment_ready где amount — интент, не факт.
    """
    if not text:
        return None
    # Только нота с реальным платежом содержит transactionId
    if "transactionId" not in text:
        return None
    m = re.search(r'amount:\s*([\d.]+)', text)
    return float(m.group(1)) if m else None

def get_utm_value(lead: dict, field_id: int) -> str:
    for cf in lead.get("custom_fields_values", []) or []:
        if cf["field_id"] == field_id:
            vals = cf.get("values", [])
            return vals[0]["value"].lower().strip() if vals else ""
    return ""

def is_non_paid(lead: dict) -> bool:
    src = get_utm_value(lead, UTM_SOURCE_FIELD)
    med = get_utm_value(lead, UTM_MEDIUM_FIELD)
    return src in NON_PAID_SOURCES or med in NON_PAID_MEDIUMS

# ── Расчёт мотивации Фила ──────────────────────────────────────────────────────

def calc_phil(ts_from: int, ts_to: int) -> dict:
    print("  Загружаю сделки из amoCRM…")
    all_deals = []
    for pid, sid, name in [
        (MD_PIPELINE_ID, MD_PAID_STATUS, "Money-Dick"),
        (PR_PIPELINE_ID, PR_PAID_STATUS, "PetRock"),
    ]:
        deals = fetch_paid_deals(pid, sid, ts_from, ts_to)
        print(f"    {name}: {len(deals)} оплаченных сделок")
        for d in deals:
            d["_pipeline"] = name
        all_deals.extend(deals)

    eo_revenue    = 0.0
    non_paid_rev  = 0.0
    deal_details  = []

    for deal in all_deals:
        notes = fetch_deal_notes(deal["id"])
        amount = 0.0
        for note in notes:
            text = (note.get("params", {}) or {}).get("text", "")
            amt  = parse_payment_note(text)
            if amt is not None:
                amount += amt

        # Fallback: поле price в сделке
        if amount == 0:
            amount = float(deal.get("price", 0) or 0)

        src      = get_utm_value(deal, UTM_SOURCE_FIELD)
        med      = get_utm_value(deal, UTM_MEDIUM_FIELD)
        non_paid = is_non_paid(deal)

        eo_revenue   += amount
        if non_paid:
            non_paid_rev += amount

        deal_details.append({
            "name":       deal["name"],
            "pipeline":   deal["_pipeline"],
            "amount":     amount,
            "utm_source": src,
            "utm_medium": med,
            "non_paid":   non_paid,
        })

    eo_rate  = get_eo_rate(eo_revenue)
    np_rate  = get_non_paid_rate(eo_revenue)
    eo_bonus = eo_revenue * eo_rate
    np_bonus = non_paid_rev * np_rate
    total_bonus = eo_bonus + np_bonus

    return {
        "eo_revenue":     eo_revenue,
        "non_paid_rev":   non_paid_rev,
        "eo_rate":        eo_rate,
        "np_rate":        np_rate,
        "eo_bonus":       eo_bonus,
        "np_bonus":       np_bonus,
        "total_bonus":    total_bonus,
        "total_payout":   PHIL_SALARY + total_bonus,
        "deal_count":     len(all_deals),
        "deals":          deal_details,
    }

# ── Расчёт мотивации Кирилла ───────────────────────────────────────────────────

def fetch_kirill_from_sheet() -> dict:
    """Читает D8:E9 из листа «Медиаплан» через публичный CSV-экспорт."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEETS_ID}"
        f"/export?format=csv&gid={SHEETS_GID}"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as r:
        content = r.read().decode("utf-8")

    rows = list(csv.reader(io.StringIO(content)))

    def parse_num(s: str) -> float | None:
        s = re.sub(r"[^\d.]", "", s.replace(",", "."))
        return float(s) if s else None

    # Строка 8 = rows[7] → Регистрации   (D=cols[3], E=cols[4])
    # Строка 9 = rows[8] → CPL            (D=cols[3], E=cols[4])
    reg_row = rows[7] if len(rows) > 7 else []
    cpl_row = rows[8] if len(rows) > 8 else []

    return {
        "plan_regs":   parse_num(reg_row[3]) if len(reg_row) > 3 else None,
        "actual_regs": parse_num(reg_row[4]) if len(reg_row) > 4 else None,
        "plan_cpl":    parse_num(cpl_row[3]) if len(cpl_row) > 3 else None,
        "actual_cpl":  parse_num(cpl_row[4]) if len(cpl_row) > 4 else None,
    }

def calc_kirill(plan_cpl=None, actual_cpl=None,
                plan_regs=None, actual_regs=None) -> dict:
    if None in (plan_cpl, actual_cpl, plan_regs, actual_regs):
        print("  Загружаю данные Кирилла из Google Sheets…")
        try:
            sheet = fetch_kirill_from_sheet()
            plan_cpl    = plan_cpl    or sheet["plan_cpl"]
            actual_cpl  = actual_cpl  or sheet["actual_cpl"]
            plan_regs   = plan_regs   or sheet["plan_regs"]
            actual_regs = actual_regs or sheet["actual_regs"]
        except Exception as e:
            return {"error": f"Не удалось прочитать Google Sheets: {e}. "
                             "Используй --kirill-* аргументы."}

    if None in (plan_cpl, actual_cpl, plan_regs, actual_regs):
        return {"error": "Нет данных для Кирилла. Заполни E8:E9 в медиаплане или передай --kirill-* аргументы."}

    # CPL: чем ниже — тем лучше → % выполнения = план / факт
    cpl_pct  = (plan_cpl / actual_cpl)  if actual_cpl  else 0.0
    # Регистрации: чем выше — тем лучше → % выполнения = факт / план
    regs_pct = (actual_regs / plan_regs) if plan_regs   else 0.0

    # Оба должны выполняться — берём минимум
    min_pct = min(cpl_pct, regs_pct)
    bonus   = get_kirill_bonus(min_pct)

    return {
        "plan_cpl":    plan_cpl,
        "actual_cpl":  actual_cpl,
        "plan_regs":   plan_regs,
        "actual_regs": actual_regs,
        "cpl_pct":     cpl_pct,
        "regs_pct":    regs_pct,
        "min_pct":     min_pct,
        "bonus":       bonus,
        "total_payout": KIRILL_SALARY + bonus,
    }

# ── HTML-дашборд ───────────────────────────────────────────────────────────────

def fmt_rub(amount: float) -> str:
    return f"₽ {amount:,.0f}".replace(",", " ")

def pct_label(pct: float) -> str:
    return f"{pct * 100:.1f}%"

def tier_color(pct: float) -> str:
    if pct >= 1.0:  return "#00b894"
    if pct >= 0.90: return "#fdcb6e"
    if pct >= 0.85: return "#e17055"
    if pct >= 0.80: return "#d63031"
    return "#636e72"

BASE_CSS = """
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2d3045;
    --text: #e8eaf6;
    --muted: #8892b0;
    --green: #00b894;
    --yellow: #fdcb6e;
    --red: #e17055;
    --accent: #6c63ff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, 'Segoe UI', sans-serif; padding: 32px 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 26px; font-weight: 700; margin-bottom: 6px; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 36px; }
  .hero { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 28px 32px; margin-bottom: 28px; display: flex; justify-content: space-between; align-items: center; }
  .hero-name { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }
  .hero-total { font-size: 42px; font-weight: 800; color: var(--green); }
  .hero-breakdown { font-size: 13px; color: var(--muted); line-height: 2; margin-top: 10px; }
  .section { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 28px; margin-bottom: 24px; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; }
  .section-title { font-size: 16px; font-weight: 700; }
  .payout-chip { background: var(--accent); color: #fff; padding: 5px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
  .metric-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
  .metric { background: #12141e; border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .metric-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
  .metric-value { font-size: 22px; font-weight: 700; }
  .result-block { border: 2px solid var(--green); border-radius: 10px; padding: 18px 22px; margin: 20px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
  .bonus-row { font-size: 14px; color: var(--muted); }
  .bonus-amount { font-size: 20px; font-weight: 700; color: var(--text); margin-left: 8px; }
  .tiers-table { margin-top: 16px; }
  .tiers-table table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .tiers-table th { background: #12141e; color: var(--muted); text-align: left; padding: 8px 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; font-size: 11px; }
  .tiers-table td { padding: 8px 12px; border-top: 1px solid var(--border); }
  .active-tier td { background: #1e2235; color: var(--green); font-weight: 700; }
  .deals-toggle { color: var(--accent); cursor: pointer; font-size: 13px; border: none; background: none; margin-top: 20px; padding: 0; }
  #deals-table { display: none; margin-top: 16px; overflow-x: auto; }
  #deals-table table { width: 100%; border-collapse: collapse; font-size: 12px; }
  #deals-table th { background: #12141e; color: var(--muted); text-align: left; padding: 8px 10px; font-size: 11px; text-transform: uppercase; }
  #deals-table td { padding: 7px 10px; border-top: 1px solid var(--border); }
  .alert { background: #2d1b1b; border: 1px solid #d63031; border-radius: 8px; padding: 16px; color: #ff7675; font-size: 13px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 40px; }
"""

def _adjacent_month(month_str: str, delta: int) -> tuple[str, str]:
    """Return (YYYY-MM, label) for month_str ± delta months."""
    year, mon = map(int, month_str.split("-"))
    mon += delta
    while mon > 12:
        mon -= 12; year += 1
    while mon < 1:
        mon += 12; year -= 1
    return f"{year:04d}-{mon:02d}", f"{MONTHS_RU[mon]} {year}"

NAV_CSS = """
  .month-nav { display:flex; align-items:center; justify-content:space-between;
               margin-bottom:28px; gap:12px; }
  .nav-btn { background:var(--card); border:1px solid var(--border); border-radius:8px;
             padding:7px 16px; font-size:13px; color:var(--text); text-decoration:none;
             transition:border-color .15s; white-space:nowrap; }
  .nav-btn:hover { border-color:var(--accent); color:var(--accent); }
  .nav-disabled { color:var(--muted); cursor:default; pointer-events:none; }
  .nav-center { text-align:center; }
  .nav-current { font-size:16px; font-weight:700; }
  .nav-updated { font-size:11px; color:var(--muted); margin-top:3px; }
"""

def _html_shell(title: str, month_label: str, month_str: str, body: str,
                page_file: str = "phil.html",
                prev_month: tuple | None = None,
                next_month: tuple | None = None) -> str:
    """
    page_file  — 'phil.html' or 'kirill.html'
    prev_month — (YYYY-MM, label) | None
    next_month — (YYYY-MM, label) | None
    Links point to sibling months: ../YYYY-MM/page_file
    """
    ts = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    prev_btn = (
        f'<a class="nav-btn" href="../{prev_month[0]}/{page_file}">← {prev_month[1]}</a>'
        if prev_month else '<span class="nav-btn nav-disabled">←</span>'
    )
    next_btn = (
        f'<a class="nav-btn" href="../{next_month[0]}/{page_file}">{next_month[1]} →</a>'
        if next_month else '<span class="nav-btn nav-disabled">→</span>'
    )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {month_label}</title>
<style>{BASE_CSS}{NAV_CSS}</style>
</head>
<body>
<div class="month-nav">
  {prev_btn}
  <div class="nav-center">
    <div class="nav-current">{month_label}</div>
    <div class="nav-updated">Обновлено {ts}</div>
  </div>
  {next_btn}
</div>
{body}
<footer>W3A Motivation Calculator · {DOMAIN}</footer>
</body>
</html>"""


def generate_html_phil(month_label: str, month_str: str, phil: dict, output_path: str,
                       prev_month: tuple | None = None,
                       next_month: tuple | None = None) -> None:
    """Страница мотивации Филиппа Тимуша."""
    eo_rev   = phil.get("eo_revenue", 0)
    np_rev   = phil.get("non_paid_rev", 0)
    eo_rate  = phil.get("eo_rate", 0)
    np_rate  = phil.get("np_rate", 0)
    eo_bonus = phil.get("eo_bonus", 0)
    np_bonus = phil.get("np_bonus", 0)
    total_b  = phil.get("total_bonus", 0)
    payout   = phil.get("total_payout", PHIL_SALARY)

    deals_rows = ""
    for d in phil.get("deals", []):
        np_badge = (
            '<span style="background:#00b894;color:#fff;padding:2px 6px;'
            'border-radius:4px;font-size:11px">non-paid</span>'
            if d["non_paid"] else
            '<span style="background:#2d3045;color:#8892b0;padding:2px 6px;'
            'border-radius:4px;font-size:11px">paid</span>'
        )
        deals_rows += (
            f"<tr><td>{d['name']}</td><td>{d['pipeline']}</td>"
            f"<td>{fmt_rub(d['amount'])}</td>"
            f"<td>{d['utm_source']}</td><td>{d['utm_medium']}</td>"
            f"<td>{np_badge}</td></tr>\n"
        )

    body = f"""
<div class="hero">
  <div>
    <div class="hero-name">Филипп Тимуш &nbsp;·&nbsp; Entry Offer</div>
    <div class="hero-total">{fmt_rub(payout)}</div>
    <div class="hero-breakdown">
      Оклад: {fmt_rub(PHIL_SALARY)}<br>
      Бонус EO ({int(eo_rate*100)}%): {fmt_rub(eo_bonus)}<br>
      Бонус non-paid ({int(np_rate*100)}%): {fmt_rub(np_bonus)}
    </div>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">Entry Offer (Money-Dick + PetRock)</div>
    <div class="payout-chip">{fmt_rub(eo_bonus)}</div>
  </div>
  <div class="metric-grid">
    <div class="metric">
      <div class="metric-label">Выручка EO</div>
      <div class="metric-value">{fmt_rub(eo_rev)}</div>
    </div>
    <div class="metric" style="border-left:3px solid var(--accent)">
      <div class="metric-label">Текущий %</div>
      <div class="metric-value" style="color:var(--accent)">{int(eo_rate*100)}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Бонус EO</div>
      <div class="metric-value">{fmt_rub(eo_bonus)}</div>
    </div>
  </div>
  <div class="tiers-table">
    <table><thead><tr><th>Выручка EO за месяц</th><th>% бонуса</th></tr></thead><tbody>
      <tr class="{'active-tier' if eo_rev>=5_000_000 else ''}"><td>5 млн+</td><td>1%</td></tr>
      <tr class="{'active-tier' if 1_000_000<=eo_rev<5_000_000 else ''}"><td>1 млн – 5 млн</td><td>2%</td></tr>
      <tr class="{'active-tier' if eo_rev<1_000_000 else ''}"><td>до 1 млн</td><td>4%</td></tr>
    </tbody></table>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">Non-paid base (email / TG / боты)</div>
    <div class="payout-chip">{fmt_rub(np_bonus)}</div>
  </div>
  <div class="metric-grid">
    <div class="metric">
      <div class="metric-label">Выручка non-paid</div>
      <div class="metric-value">{fmt_rub(np_rev)}</div>
    </div>
    <div class="metric" style="border-left:3px solid var(--green)">
      <div class="metric-label">% (привязан к тиру EO)</div>
      <div class="metric-value" style="color:var(--green)">{int(np_rate*100)}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Бонус non-paid</div>
      <div class="metric-value">{fmt_rub(np_bonus)}</div>
    </div>
  </div>
  <div class="tiers-table">
    <table><thead><tr><th>Тир EO</th><th>% non-paid</th></tr></thead><tbody>
      <tr class="{'active-tier' if eo_rev>=5_000_000 else ''}"><td>5 млн+</td><td>2%</td></tr>
      <tr class="{'active-tier' if 1_000_000<=eo_rev<5_000_000 else ''}"><td>1 млн – 5 млн</td><td>4%</td></tr>
      <tr class="{'active-tier' if eo_rev<1_000_000 else ''}"><td>до 1 млн</td><td>8%</td></tr>
    </tbody></table>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">Оплаченные сделки ({phil.get('deal_count', 0)})</div>
  </div>
  <button class="deals-toggle" onclick="var t=document.getElementById('deals-table'); t.style.display=t.style.display==='none'?'block':'none'">
    ▾ Показать / скрыть список
  </button>
  <div id="deals-table">
    <table style="margin-top:14px">
      <thead><tr><th>Сделка</th><th>Воронка</th><th>Сумма</th><th>utm_source</th><th>utm_medium</th><th>Тип</th></tr></thead>
      <tbody>{deals_rows or "<tr><td colspan='6' style='color:var(--muted);text-align:center;padding:16px'>Сделок в этом месяце не найдено</td></tr>"}</tbody>
    </table>
  </div>
</div>
"""
    html = _html_shell("Мотивация — Филипп Тимуш", month_label, month_str, body,
                       page_file="phil.html",
                       prev_month=prev_month, next_month=next_month)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"  Фил:    {output_path}")


def generate_html_kirill(month_label: str, month_str: str, kirill: dict, output_path: str,
                         prev_month: tuple | None = None,
                         next_month: tuple | None = None) -> None:
    """Страница мотивации Кирилла Осипова."""
    if "error" in kirill:
        body = f"""
<div class="hero">
  <div>
    <div class="hero-name">Кирилл Осипов &nbsp;·&nbsp; Трафик</div>
    <div class="hero-total" style="color:var(--muted)">—</div>
  </div>
</div>
<div class="section"><div class="alert">{kirill['error']}</div></div>
"""
    else:
        cpl_color  = tier_color(kirill["cpl_pct"])
        regs_color = tier_color(kirill["regs_pct"])
        min_color  = tier_color(kirill["min_pct"])
        payout     = kirill["total_payout"]
        bonus      = kirill["bonus"]

        body = f"""
<div class="hero">
  <div>
    <div class="hero-name">Кирилл Осипов &nbsp;·&nbsp; Трафик</div>
    <div class="hero-total">{fmt_rub(payout)}</div>
    <div class="hero-breakdown">
      Оклад: {fmt_rub(KIRILL_SALARY)}<br>
      Бонус за трафик: {fmt_rub(bonus)}<br>
      Определяющий %: {pct_label(kirill['min_pct'])}
    </div>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">CPL (стоимость регистрации)</div>
    <div class="payout-chip" style="background:{cpl_color}">{pct_label(kirill['cpl_pct'])}</div>
  </div>
  <div class="metric-grid" style="grid-template-columns:1fr 1fr 1fr">
    <div class="metric">
      <div class="metric-label">План CPL</div>
      <div class="metric-value">{fmt_rub(kirill['plan_cpl'])}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Факт CPL</div>
      <div class="metric-value">{fmt_rub(kirill['actual_cpl'])}</div>
    </div>
    <div class="metric" style="border-left:3px solid {cpl_color}">
      <div class="metric-label">% выполнения</div>
      <div class="metric-value" style="color:{cpl_color}">{pct_label(kirill['cpl_pct'])}</div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">Регистрации</div>
    <div class="payout-chip" style="background:{regs_color}">{pct_label(kirill['regs_pct'])}</div>
  </div>
  <div class="metric-grid" style="grid-template-columns:1fr 1fr 1fr">
    <div class="metric">
      <div class="metric-label">План</div>
      <div class="metric-value">{kirill['plan_regs']:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Факт</div>
      <div class="metric-value">{kirill['actual_regs']:,.0f}</div>
    </div>
    <div class="metric" style="border-left:3px solid {regs_color}">
      <div class="metric-label">% выполнения</div>
      <div class="metric-value" style="color:{regs_color}">{pct_label(kirill['regs_pct'])}</div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <div class="section-title">Бонус за трафик</div>
    <div class="payout-chip">{fmt_rub(bonus)}</div>
  </div>
  <div class="result-block" style="border-color:{min_color}">
    <div>Определяющий % (минимум CPL и рег.): <strong style="color:{min_color}">{pct_label(kirill['min_pct'])}</strong></div>
    <div class="bonus-row">Бонус: <span class="bonus-amount">{fmt_rub(bonus)}</span></div>
  </div>
  <div class="tiers-table">
    <table><thead><tr><th>% выполнения плана</th><th>Бонус</th></tr></thead><tbody>
      <tr class="{'active-tier' if kirill['min_pct']>=1.00 else ''}"><td>100%+</td><td>₽ 45 000</td></tr>
      <tr class="{'active-tier' if 0.90<=kirill['min_pct']<1.00 else ''}"><td>90–99%</td><td>₽ 35 000</td></tr>
      <tr class="{'active-tier' if 0.85<=kirill['min_pct']<0.90 else ''}"><td>85–89%</td><td>₽ 25 000</td></tr>
      <tr class="{'active-tier' if 0.80<=kirill['min_pct']<0.85 else ''}"><td>80–84%</td><td>₽ 20 000</td></tr>
      <tr class="{'active-tier' if kirill['min_pct']<0.80 else ''}"><td>ниже 80%</td><td>₽ 0</td></tr>
    </tbody></table>
  </div>
</div>
"""
    html = _html_shell("Мотивация — Кирилл Осипов", month_label, month_str, body,
                       page_file="kirill.html",
                       prev_month=prev_month, next_month=next_month)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"  Кирилл: {output_path}")

# ── CLI ────────────────────────────────────────────────────────────────────────

def _write_index(out_dir: Path, latest_month_str: str, latest_label: str) -> None:
    """Обновляет index.html — список всех сгенерированных месяцев."""
    # Собираем все существующие папки YYYY-MM
    months = sorted(
        [d.name for d in out_dir.iterdir()
         if d.is_dir() and len(d.name) == 7 and d.name[4] == "-"],
        reverse=True,
    )
    month_links = ""
    for m in months:
        year, mon = map(int, m.split("-"))
        label = f"{MONTHS_RU[mon]} {year}"
        is_latest = " (текущий)" if m == latest_month_str else ""
        month_links += (
            f'<li><a href="{m}/phil.html">Филипп</a>'
            f' &nbsp;·&nbsp; <a href="{m}/kirill.html">Кирилл</a>'
            f' &nbsp;— <span class="month-name">{label}{is_latest}</span></li>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>W3A Motivation</title>
<style>
  :root{{--bg:#0f1117;--card:#1a1d27;--border:#2d3045;--text:#e8eaf6;--muted:#8892b0;--accent:#6c63ff;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);color:var(--text);font-family:-apple-system,'Segoe UI',sans-serif;
        display:flex;flex-direction:column;align-items:center;justify-content:center;
        min-height:100vh;padding:32px 24px;}}
  h1{{font-size:22px;font-weight:700;margin-bottom:6px;}}
  .sub{{color:var(--muted);font-size:13px;margin-bottom:36px;}}
  .months{{background:var(--card);border:1px solid var(--border);border-radius:12px;
           padding:24px;width:100%;max-width:480px;}}
  .months h2{{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
              margin-bottom:16px;}}
  ul{{list-style:none;}}
  li{{padding:10px 0;border-top:1px solid var(--border);font-size:14px;}}
  li:first-child{{border-top:none;}}
  a{{color:var(--accent);text-decoration:none;}}
  a:hover{{text-decoration:underline;}}
  .month-name{{color:var(--muted);font-size:13px;}}
  footer{{color:var(--muted);font-size:12px;margin-top:40px;}}
</style>
</head>
<body>
<h1>Мотивация W3A</h1>
<p class="sub">Выбери месяц и сотрудника</p>
<div class="months">
  <h2>Доступные месяцы</h2>
  <ul>{month_links or "<li style='color:var(--muted)'>Нет данных</li>"}</ul>
</div>
<footer>Обновляется автоматически 1-го числа каждого месяца</footer>
</body>
</html>"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def month_timestamps(month_str: str) -> tuple[int, int, str]:
    """Returns (ts_from, ts_to, label) for a given 'YYYY-MM' string."""
    year, mon = map(int, month_str.split("-"))
    dt_from = datetime.datetime(year, mon, 1, tzinfo=datetime.timezone.utc)
    if mon == 12:
        dt_to = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
    else:
        dt_to = datetime.datetime(year, mon + 1, 1, tzinfo=datetime.timezone.utc)
    label = f"{MONTHS_RU[mon]} {year}"
    return int(dt_from.timestamp()), int(dt_to.timestamp()) - 1, label

def main():
    parser = argparse.ArgumentParser(description="W3A Motivation Calculator")
    parser.add_argument("--month", default=None,
                        help="Месяц расчёта в формате YYYY-MM (по умолчанию — текущий)")
    parser.add_argument("--kirill-plan-cpl",    type=float)
    parser.add_argument("--kirill-actual-cpl",  type=float)
    parser.add_argument("--kirill-plan-regs",   type=float)
    parser.add_argument("--kirill-actual-regs", type=float)
    parser.add_argument("--out-dir", default=".",
                        help="Директория для сохранения HTML-файлов (по умолчанию — текущая)")
    args = parser.parse_args()

    if not TOKEN:
        print("❌ AMO_TOKEN не найден. Добавь его в .env или переменную окружения.")
        return

    if args.month:
        month_str = args.month
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
        month_str = now.strftime("%Y-%m")

    ts_from, ts_to, month_label = month_timestamps(month_str)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n📅 Расчёт мотивации за {month_label}\n")

    print("📊 Фил — загружаю данные из amoCRM…")
    phil = calc_phil(ts_from, ts_to)
    print(f"   Entry Offer выручка: {fmt_rub(phil['eo_revenue'])}")
    print(f"   Non-paid выручка:    {fmt_rub(phil['non_paid_rev'])}")
    print(f"   Бонус EO:            {fmt_rub(phil['eo_bonus'])}")
    print(f"   Бонус non-paid:      {fmt_rub(phil['np_bonus'])}")
    print(f"   Выплата:             {fmt_rub(phil['total_payout'])}")

    print("\n📊 Кирилл — загружаю данные из медиаплана…")
    kirill = calc_kirill(
        plan_cpl    = args.kirill_plan_cpl,
        actual_cpl  = args.kirill_actual_cpl,
        plan_regs   = args.kirill_plan_regs,
        actual_regs = args.kirill_actual_regs,
    )
    if "error" in kirill:
        print(f"   ⚠️  {kirill['error']}")
    else:
        print(f"   CPL:    план {fmt_rub(kirill['plan_cpl'])} → факт {fmt_rub(kirill['actual_cpl'])} ({pct_label(kirill['cpl_pct'])})")
        print(f"   Рег.:   план {kirill['plan_regs']:.0f} → факт {kirill['actual_regs']:.0f} ({pct_label(kirill['regs_pct'])})")
        print(f"   Мин. %: {pct_label(kirill['min_pct'])} → бонус {fmt_rub(kirill['bonus'])}")
        print(f"   Выплата: {fmt_rub(kirill['total_payout'])}")

    # Папка per-month: out_dir/YYYY-MM/
    month_dir = out_dir / month_str
    month_dir.mkdir(parents=True, exist_ok=True)

    prev_month = _adjacent_month(month_str, -1)
    next_month = _adjacent_month(month_str, +1)
    # Показываем «следующий» только если он не в будущем
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    if next_month[0] > now_str:
        next_month = None

    print("\n🖥️  Генерирую HTML-страницы…")
    phil_path   = month_dir / "phil.html"
    kirill_path = month_dir / "kirill.html"
    generate_html_phil(month_label, month_str, phil, str(phil_path),
                       prev_month=prev_month, next_month=next_month)
    generate_html_kirill(month_label, month_str, kirill, str(kirill_path),
                         prev_month=prev_month, next_month=next_month)

    # Обновляем index.html → редирект на последний сгенерированный месяц
    index_path = out_dir / "index.html"
    _write_index(out_dir, month_str, month_label)

    print(f"\n✅ Готово!")
    print(f"   Фил:    {phil_path.resolve()}")
    print(f"   Кирилл: {kirill_path.resolve()}")

if __name__ == "__main__":
    main()
