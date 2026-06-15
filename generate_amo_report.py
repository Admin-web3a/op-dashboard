#!/usr/bin/env python3
"""
amoCRM Daily ОП Dashboard Generator
Generates docs/index.html with Chart.js visualizations.
Run daily via GitHub Actions (cron 0 4 * * * = 7:00 MSK).
"""

import urllib.request
import json
import os
import datetime
from collections import Counter, defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN  = os.environ["AMO_TOKEN"]
DOMAIN = "simmihur.amocrm.ru"

SOURCE_FIELD_ID = 1321741
SOURCE_ENUM_ID  = 953633    # Анкета перезаписи 06.2026
UPDATED_FROM    = 1743465600  # 2026-04-01

MANAGERS = {
    12377210: "Никита Саламатин",
    11176694: "Наталья",
     6461602: "Зверева Елена",
    11181290: "Сергей",
     9948090: "Денис Криницын",
    11068965: "Максим Лисевский",
    10293970: "Влад",
    12738086: "Кирилл",
    11356530: "Денис",
}

# Группы для менеджерских диаграмм и раскраски
STATUS_GROUPS = {
    "НДЗ":                       "ndz",
    "Входящий чекин":            "incoming",
    "ОМ назначен чекин":         "incoming",
    "Новый лид":                 "new_lead",
    "ом назначен":               "om",
    "ОМ назначен":               "om",
    "Взято в работу":            "in_work",
    "Контакт установлен":        "contact",
    "Квалифицирован":            "qualified",
    "Оффер озвучен":             "offer",
    "Отложенный спрос":          "delayed",
    "Выставлен счет":            "invoiced",
    "Экскурсия":                 "excursion",
    "Внутренняя рассрочка":      "sale",
    "Успешно реализовано":       "sale",
    "Закрыто и не реализовано":  "lost",
}

# Для менеджерских стэк-баров объединяем в 5 визуальных групп
FUNNEL_ORDER = [
    "Входящий чекин",
    "ОМ назначен чекин",
    "Новый лид",
    "ом назначен",
    "Взято в работу",
    "НДЗ",
    "Контакт установлен",
    "Квалифицирован",
    "Экскурсия",
    "Оффер озвучен",
    "Отложенный спрос",
    "Выставлен счет",
    "Внутренняя рассрочка",
    "Успешно реализовано",
    "Закрыто и не реализовано",
]

VIZ_GROUP = {
    "incoming":    "incoming",
    "new_lead":    "new_lead",
    "om":          "om",
    "in_work":     "in_work",
    "contact":     "contact",
    "qualified":   "qualified",
    "ndz":         "ndz",
    "offer":       "offer",
    "delayed":     "delayed",
    "invoiced":    "sale",
    "excursion":   "sale",
    "installment": "sale",
    "sale":        "sale",
    "lost":        "lost",
}

VIZ_LABELS = {
    "incoming":  "Входящие",
    "new_lead":  "Новый лид",
    "om":        "ОМ назначен",
    "in_work":   "Взято в работу",
    "contact":   "Контакт установлен",
    "qualified": "Квалифицирован",
    "ndz":       "НДЗ",
    "offer":     "Оффер озвучен",
    "delayed":   "Отложен",
    "sale":      "Продажи+",
    "lost":      "Потеряно",
}
VIZ_COLORS = {
    "incoming":  "#74b9ff",
    "new_lead":  "#0984e3",
    "om":        "#6c5ce7",
    "in_work":   "#00cec9",
    "contact":   "#ffd32a",
    "qualified": "#ff6b81",
    "ndz":       "#f5a623",
    "offer":     "#7ed6df",
    "delayed":   "#a29bfe",
    "sale":      "#6ab04c",
    "lost":      "#eb4d4b",
}
VIZ_ORDER  = ["incoming", "new_lead", "om", "in_work", "contact", "qualified", "ndz", "offer", "delayed", "sale", "lost"]

# ── API ───────────────────────────────────────────────────────────────────────

def api_get(path):
    url = f"https://{DOMAIN}/api/v4/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_pipelines():
    data = api_get("leads/pipelines")
    statuses = {}
    for p in data.get("_embedded", {}).get("pipelines", []):
        for s in p.get("_embedded", {}).get("statuses", []):
            name = s["name"]
            statuses[s["id"]] = {
                "name":     name,
                "pipeline": p["name"],
                "group":    STATUS_GROUPS.get(name, "active"),
            }
    return statuses

def fetch_filtered_leads(statuses):
    filtered = []
    consecutive_empty = 0
    page = 1
    while True:
        path = (f"leads?limit=250&page={page}"
                f"&order[updated_at]=desc&filter[updated_at][from]={UPDATED_FROM}")
        data = api_get(path)
        batch = data.get("_embedded", {}).get("leads", [])
        if not batch:
            break
        matched = 0
        for lead in batch:
            for cf in (lead.get("custom_fields_values") or []):
                if cf.get("field_id") == SOURCE_FIELD_ID:
                    for v in cf.get("values", []):
                        if v.get("enum_id") == SOURCE_ENUM_ID:
                            filtered.append(lead)
                            matched += 1
                            break
        consecutive_empty = 0 if matched else consecutive_empty + 1
        if consecutive_empty >= 5 or len(batch) < 250:
            break
        page += 1
    return filtered

# Funnel position for cumulative conversion (higher = further in funnel, 0 = excluded)
FUNNEL_POS = {
    "incoming":  1,
    "new_lead":  2,
    "om":        3,
    "in_work":   4,   # Взято в работу  ← Stage A
    "contact":   5,   # Контакт установлен ← Stage B
    "qualified": 6,
    "offer":     7,
    "delayed":   7,
    "invoiced":  8,
    "excursion": 8,
    "sale":      9,
    "ndz":       0,   # excluded: не является прогрессом
    "lost":      0,   # excluded
}

def compute_conversion_by_day(leads, statuses, tz_msk, start_date, today):
    """Cumulative conversion Взято→Контакт grouped by lead creation date.
    A lead is counted as 'reached stage X' if its current status has funnel
    position >= X (assumes monotone progression, matching the screenshot's note).
    """
    day_vzv = Counter()   # date -> leads that reached "Взято в работу" or higher
    day_kon = Counter()   # date -> leads that reached "Контакт установлен" or higher

    for lead in leads:
        grp = statuses.get(lead.get("status_id"), {}).get("group", "active")
        pos = FUNNEL_POS.get(grp, 0)
        if pos < 4:          # didn't reach "Взято в работу"
            continue
        created_ts = lead.get("created_at")
        if not created_ts:
            continue
        lead_date = datetime.datetime.fromtimestamp(created_ts, tz=tz_msk).date()
        day_key = lead_date.strftime("%d.%m")
        # Only show dates from start_date onwards
        if lead_date < start_date or lead_date > today:
            continue
        day_vzv[day_key] += 1
        if pos >= 5:         # reached "Контакт установлен" or higher
            day_kon[day_key] += 1

    all_dates = []
    d = start_date
    while d <= today:
        all_dates.append(d.strftime("%d.%m"))
        d += datetime.timedelta(days=1)

    vzv_vals = [day_vzv.get(d, 0) for d in all_dates]
    kon_pct  = [
        round(day_kon.get(d, 0) / day_vzv[d] * 100) if day_vzv.get(d) else 0
        for d in all_dates
    ]
    return all_dates, vzv_vals, kon_pct


def fetch_overdue_tasks():
    now_ts = int(datetime.datetime.utcnow().timestamp())
    try:
        tasks = []
        page = 1
        while True:
            path = f"tasks?limit=250&page={page}&filter[is_completed]=0&filter[complete_till][to]={now_ts}"
            data = api_get(path)
            batch = data.get("_embedded", {}).get("tasks", [])
            if not batch:
                break
            tasks.extend(batch)
            if len(batch) < 250:
                break
            page += 1
    except Exception:
        return {}
    counts = Counter()
    for t in tasks:
        uid = t.get("responsible_user_id")
        if uid in MANAGERS:
            counts[uid] += 1
    return dict(counts)

# ── Build ─────────────────────────────────────────────────────────────────────

def build_report():
    print("Fetching pipelines…")
    statuses = fetch_pipelines()

    print("Fetching leads…")
    leads = fetch_filtered_leads(statuses)
    total = len(leads)
    total_price = sum(l.get("price") or 0 for l in leads)

    # Counts by fine-grained group
    group_counts = Counter()
    status_counts = Counter()
    for lead in leads:
        sid = lead.get("status_id")
        status_counts[sid] += 1
        group = statuses.get(sid, {}).get("group", "active")
        group_counts[group] += 1

    # Per-manager (managers only) using viz groups
    mgr_viz = defaultdict(Counter)
    for lead in leads:
        uid = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        sid = lead.get("status_id")
        grp = statuses.get(sid, {}).get("group", "active")
        vg  = VIZ_GROUP.get(grp, "active")
        mgr_viz[uid][vg] += 1

    print("Fetching overdue tasks…")
    overdue = fetch_overdue_tasks()

    print("Computing conversion (Взято → Контакт) by creation date…")
    _tz_msk     = datetime.timezone(datetime.timedelta(hours=3))
    _start_date = datetime.date(2026, 6, 6)
    _today      = datetime.datetime.now(_tz_msk).date()
    conv_dates, conv_vzv, conv_pct = compute_conversion_by_day(leads, statuses, _tz_msk, _start_date, _today)

    # Sorted status list for funnel chart — fixed funnel order
    name_to_pos = {name: i for i, name in enumerate(FUNNEL_ORDER)}
    status_list = []
    for sid, cnt in status_counts.items():
        info = statuses.get(sid, {})
        name = info.get("name", f"?({sid})")
        status_list.append({
            "name":     name,
            "count":    cnt,
            "group":    info.get("group", "active"),
            "pipeline": info.get("pipeline", ""),
            "_order":   name_to_pos.get(name, 999),
        })
    sorted_statuses = sorted(status_list, key=lambda x: x["_order"])
    for s in sorted_statuses:
        s.pop("_order", None)

    # Daily lead counts from June 6 onwards
    tz_msk = datetime.timezone(datetime.timedelta(hours=3))
    start_date = datetime.date(2026, 6, 6)
    today = datetime.datetime.now(tz_msk).date()
    daily_counts = {}
    d = start_date
    while d <= today:
        daily_counts[d.strftime("%d.%m")] = 0
        d += datetime.timedelta(days=1)

    for lead in leads:
        created_ts = lead.get("created_at")
        if created_ts:
            lead_date = datetime.datetime.fromtimestamp(created_ts, tz=tz_msk).date()
            key = lead_date.strftime("%d.%m")
            if key in daily_counts:
                daily_counts[key] += 1

    # Custom field IDs for questionnaire fields
    CAPITAL_FIELD_ID = 1304047
    READY_FIELD_ID   = 1317111

    # Capital order for display
    CAPITAL_ORDER = ["$0-5,000", "до $5,000", "$5,000-50,000", "$50,000-100,000",
                     "$100,000-500,000", "$500,000-1,000,000", "$1,000,000+", "Неизвестно"]

    capital_counts = Counter()
    ready_counts   = Counter()
    # daily capital breakdown: {date_key: {capital_val: count}}
    daily_capital  = {d: Counter() for d in daily_counts}

    for lead in leads:
        cap_val = None
        rdy_val = None
        for cf in (lead.get("custom_fields_values") or []):
            fid  = cf.get("field_id")
            vals = cf.get("values") or []
            if fid == CAPITAL_FIELD_ID and vals:
                cap_val = vals[0].get("value", "?")
            elif fid == READY_FIELD_ID and vals:
                rdy_val = vals[0].get("value", "?")
        if cap_val:
            capital_counts[cap_val] += 1
            created_ts = lead.get("created_at")
            if created_ts:
                lead_date = datetime.datetime.fromtimestamp(created_ts, tz=tz_msk).date()
                day_key = lead_date.strftime("%d.%m")
                if day_key in daily_capital:
                    daily_capital[day_key][cap_val] += 1
        if rdy_val:
            ready_counts[rdy_val] += 1

    # Sort capital by predefined order
    capital_labels = [k for k in CAPITAL_ORDER if k in capital_counts]
    for k in capital_counts:
        if k not in capital_labels:
            capital_labels.append(k)
    capital_values = [capital_counts[k] for k in capital_labels]

    # Daily capital: list of values per capital tier, aligned to daily_labels
    daily_cap_labels = list(daily_counts.keys())
    daily_cap_data   = {
        cap: [daily_capital[d].get(cap, 0) for d in daily_cap_labels]
        for cap in capital_labels
    }

    ready_labels = list(ready_counts.keys())
    ready_values = [ready_counts[k] for k in ready_labels]

    # per-manager detailed group counts for table
    mgr_detail = {}
    for uid, cnts in mgr_viz.items():
        mgr_detail[str(uid)] = dict(cnts)

    return {
        "updated_at":       datetime.datetime.now(tz_msk).strftime("%d.%m.%Y %H:%M МСК"),
        "total":            total,
        "total_price":      total_price,
        "group_counts":     dict(group_counts),
        "sorted_statuses":  sorted_statuses,
        "managers":         MANAGERS,
        "mgr_viz":          {str(uid): dict(cnts) for uid, cnts in mgr_viz.items()},
        "overdue":          {str(uid): cnt for uid, cnt in overdue.items()},
        "daily_labels":     list(daily_counts.keys()),
        "daily_values":     list(daily_counts.values()),
        "mgr_detail":       mgr_detail,
        "capital_labels":   capital_labels,
        "capital_values":   capital_values,
        "daily_cap_labels": daily_cap_labels,
        "daily_cap_data":   daily_cap_data,
        "ready_labels":     ready_labels,
        "ready_values":     ready_values,
        "conv_dates":       conv_dates,
        "conv_vzv":         conv_vzv,
        "conv_pct":         conv_pct,
    }

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ОП Dashboard — Анкета перезаписи 06.2026</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;
    --text:#e8eaf0;--muted:#8b8fa8;--accent:#4f8ef7;
    --green:#6ab04c;--orange:#f5a623;--red:#eb4d4b;--blue:#7ed6df;--purple:#a29bfe;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;padding:24px}}
  h1{{font-size:20px;font-weight:600;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:600;margin:32px 0 14px;color:var(--text)}}
  .meta{{color:var(--muted);font-size:12px;margin-bottom:28px}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:8px}}
  .stat{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}}
  .stat-value{{font-size:28px;font-weight:700;line-height:1}}
  .stat-label{{color:var(--muted);font-size:11px;margin-top:6px;text-transform:uppercase;letter-spacing:.04em}}
  .stat.accent .stat-value{{color:var(--accent)}}
  .stat.orange .stat-value{{color:var(--orange)}}
  .stat.green  .stat-value{{color:var(--green)}}
  .stat.blue   .stat-value{{color:var(--blue)}}
  .stat.purple .stat-value{{color:var(--purple)}}
  .stat.red    .stat-value{{color:var(--red)}}
  .chart-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  @media(max-width:700px){{.grid2{{grid-template-columns:1fr}}}}
  table{{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
  th{{background:#22253a;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:10px 14px;text-align:left}}
  td{{padding:9px 14px;border-top:1px solid var(--border)}}
  tr:hover td{{background:#1e2133}}
  .tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
  .tag-incoming{{background:#1a2f4a;color:#74b9ff}}
  .tag-new-lead{{background:#0d2540;color:#0984e3}}
  .tag-om{{background:#221a40;color:#6c5ce7}}
  .tag-in-work{{background:#0a3030;color:#00cec9}}
  .tag-contact{{background:#3a3000;color:#ffd32a}}
  .tag-qualified{{background:#3a1020;color:#ff6b81}}
  .tag-ndz{{background:#3a2800;color:var(--orange)}}
  .tag-offer{{background:#0a2e30;color:var(--blue)}}
  .tag-delayed{{background:#2a1a4a;color:#a29bfe}}
  .tag-sale{{background:#1a2e0a;color:var(--green)}}
  .tag-lost{{background:#2e0a0a;color:var(--red)}}
  .num{{text-align:right;font-variant-numeric:tabular-nums}}
</style>
</head>
<body>
<h1>ОП Dashboard — Анкета перезаписи 06.2026</h1>
<p class="meta">Источник: amoCRM simmihur &nbsp;·&nbsp; Обновлено: {updated_at}</p>

<div class="stats">
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Всего лидов</div></div>
  <div class="stat accent"><div class="stat-value">{active}</div><div class="stat-label">В работе</div></div>
  <div class="stat orange"><div class="stat-value">{ndz}</div><div class="stat-label">НДЗ</div></div>
  <div class="stat blue"><div class="stat-value">{offer_ozv}</div><div class="stat-label">Оффер озвучен</div></div>
  <div class="stat blue"><div class="stat-value">{delayed}</div><div class="stat-label">Отложенный спрос</div></div>
  <div class="stat purple"><div class="stat-value">{excursion}</div><div class="stat-label">Экскурсия</div></div>
  <div class="stat purple"><div class="stat-value">{invoiced}</div><div class="stat-label">Выставлен счет</div></div>
  <div class="stat green"><div class="stat-value">{sales}</div><div class="stat-label">Продажи</div></div>
  <div class="stat"><div class="stat-value">{conv_pct}%</div><div class="stat-label">Конверсия в продажу</div></div>
  <div class="stat"><div class="stat-value">{price}</div><div class="stat-label">Сумма сделок, ₽</div></div>
</div>

<h2>Лиды по дням (с 6 июня)</h2>
<div class="chart-card" style="height:200px"><canvas id="dailyChart"></canvas></div>

<h2>Лиды по капиталу по дням</h2>
<div class="chart-card" style="height:260px"><canvas id="dailyCapChart"></canvas></div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;align-items:start">
  <div>
    <h2>Капитал клиентов</h2>
    <div class="chart-card" style="height:320px"><canvas id="capitalChart"></canvas></div>
  </div>
  <div>
    <h2>Готовность присоединиться</h2>
    <div class="chart-card" style="height:320px"><canvas id="readyChart"></canvas></div>
  </div>
</div>

<h2>Распределение по статусам воронки</h2>
<div class="chart-card" style="height:420px"><canvas id="funnelChart"></canvas></div>

<h2>Конверсия: Взято в работу → Контакт установлен</h2>
<div class="chart-card" style="height:320px"><canvas id="convFunnelChart"></canvas></div>

<h2>Лиды по менеджерам</h2>
<div class="chart-card" style="height:600px"><canvas id="mgrChart"></canvas></div>

<h2>Просроченные задачи по менеджерам</h2>
<div class="chart-card" style="height:600px"><canvas id="overdueChart"></canvas></div>

<h2>Детализация по менеджерам</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr>
    <th>Менеджер</th>
    <th class="num">Всего</th>
    <th class="num">Входящие</th>
    <th class="num">Новый лид</th>
    <th class="num">ОМ назначен</th>
    <th class="num">Взято в работу</th>
    <th class="num">Контакт уст.</th>
    <th class="num">Квалифицирован</th>
    <th class="num">НДЗ</th>
    <th class="num">Оффер озвучен</th>
    <th class="num">Отложен</th>
    <th class="num">Продажи+</th>
    <th class="num">Потеряно</th>
    <th class="num">Просрочено</th>
  </tr></thead>
  <tbody id="mgrTable"></tbody>
</table>
</div>

<script>
const DATA = {json_data};
const VCOLORS = {{incoming:"#74b9ff",new_lead:"#0984e3",om:"#6c5ce7",in_work:"#00cec9",contact:"#ffd32a",qualified:"#ff6b81",ndz:"#f5a623",offer:"#7ed6df",delayed:"#a29bfe",sale:"#6ab04c",lost:"#eb4d4b"}};
const VLABELS = {{incoming:"Входящие",new_lead:"Новый лид",om:"ОМ назначен",in_work:"Взято в работу",contact:"Контакт установлен",qualified:"Квалифицирован",ndz:"НДЗ",offer:"Оффер озвучен",delayed:"Отложен",sale:"Продажи+",lost:"Потеряно"}};
const VORDER  = ["incoming","new_lead","om","in_work","contact","qualified","ndz","offer","delayed","sale","lost"];
function fmt(n){{return(n||0).toLocaleString("ru-RU")}}
const base = {{
  responsive:true,
  plugins:{{legend:{{labels:{{color:"#8b8fa8",font:{{size:12}}}}}},
            tooltip:{{callbacks:{{label:c=>` ${{c.dataset.label}}: ${{fmt(c.raw)}}`}}}}}},
  scales:{{
    x:{{ticks:{{color:"#8b8fa8"}},grid:{{color:"#2a2d3a"}}}},
    y:{{ticks:{{color:"#8b8fa8"}},grid:{{color:"#2a2d3a"}}}}
  }}
}};

// Daily leads chart
new Chart(document.getElementById("dailyChart"),{{
  type:"bar",
  data:{{
    labels:DATA.daily_labels,
    datasets:[{{
      label:"Лидов за день",
      data:DATA.daily_values,
      backgroundColor:"#4f8ef7",
      borderRadius:3,
    }}]
  }},
  options:{{...base,maintainAspectRatio:false,
    plugins:{{...base.plugins,legend:{{display:false}}}},
    scales:{{
      x:{{...base.scales.x,ticks:{{color:"#e8eaf0"}}}},
      y:{{...base.scales.y,beginAtZero:true}}
    }}
  }}
}});

// Funnel
const funColors = DATA.sorted_statuses.map(s=>{{
  const m={{active:"#4f8ef7",ndz:"#f5a623",offer:"#7ed6df",delayed:"#5dade2",
            invoiced:"#a29bfe",excursion:"#a29bfe",installment:"#a29bfe",sale:"#6ab04c",lost:"#eb4d4b"}};
  return m[s.group]||"#4f8ef7";
}});
new Chart(document.getElementById("funnelChart"),{{
  type:"bar",
  data:{{
    labels:DATA.sorted_statuses.map(s=>s.name),
    datasets:[{{label:"Сделок",data:DATA.sorted_statuses.map(s=>s.count),
               backgroundColor:funColors,borderRadius:3}}]
  }},
  options:{{...base,indexAxis:"y",maintainAspectRatio:false,
    plugins:{{...base.plugins,legend:{{display:false}}}},
    scales:{{x:{{...base.scales.x}},y:{{ticks:{{color:"#e8eaf0",font:{{size:11}}}},grid:{{color:"#2a2d3a"}}}}}}
  }}
}});

// Managers stacked
const mgrIds=Object.keys(DATA.mgr_viz).sort((a,b)=>{{
  const ta=Object.values(DATA.mgr_viz[a]).reduce((s,v)=>s+v,0);
  const tb=Object.values(DATA.mgr_viz[b]).reduce((s,v)=>s+v,0);
  return tb-ta;
}});
new Chart(document.getElementById("mgrChart"),{{
  type:"bar",
  data:{{
    labels:mgrIds.map(id=>DATA.managers[id]||id),
    datasets:VORDER.map(g=>({{
      label:VLABELS[g],
      data:mgrIds.map(id=>(DATA.mgr_viz[id]||{{}})[g]||0),
      backgroundColor:VCOLORS[g],borderRadius:2
    }}))
  }},
  options:{{...base,maintainAspectRatio:false,scales:{{
    x:{{...base.scales.x,stacked:true}},
    y:{{...base.scales.y,stacked:true}}
  }}}}
}});

// Overdue
const ovIds=Object.keys(DATA.overdue).filter(id=>DATA.managers[id]).sort((a,b)=>DATA.overdue[b]-DATA.overdue[a]);
new Chart(document.getElementById("overdueChart"),{{
  type:"bar",
  data:{{
    labels:ovIds.map(id=>DATA.managers[id]||id),
    datasets:[{{label:"Просрочено",data:ovIds.map(id=>DATA.overdue[id]),backgroundColor:"#eb4d4b",borderRadius:3}}]
  }},
  options:{{...base,maintainAspectRatio:false,plugins:{{...base.plugins,legend:{{display:false}}}}}}
}});

// Daily capital grouped bar (% of day total)
const capColors = {{"$0-5,000":"#eb4d4b","до $5,000":"#f5a623","$5,000-50,000":"#ffd32a","$50,000-100,000":"#6ab04c","$100,000-500,000":"#00cec9","$500,000-1,000,000":"#4f8ef7","$1,000,000+":"#a29bfe","Неизвестно":"#636e72"}};
const dayTotals = DATA.daily_cap_labels.map((_,i)=>DATA.capital_labels.reduce((s,c)=>s+(DATA.daily_cap_data[c][i]||0),0));
new Chart(document.getElementById("dailyCapChart"),{{
  type:"bar",
  data:{{
    labels:DATA.daily_cap_labels,
    datasets:DATA.capital_labels.map(cap=>{{
      return {{
        label:cap,
        data:DATA.daily_cap_data[cap].map((v,i)=>dayTotals[i]?Math.round(v/dayTotals[i]*100):0),
        backgroundColor:capColors[cap]||"#999",
        borderWidth:0,
        borderRadius:2,
      }};
    }})
  }},
  options:{{
    maintainAspectRatio:false,
    plugins:{{
      legend:{{position:"top",labels:{{color:"#e8eaf0",font:{{size:11}},boxWidth:12,padding:8}}}},
      tooltip:{{
        mode:"index",intersect:false,
        callbacks:{{label:function(c){{return ` ${{c.dataset.label}}: ${{c.raw}}%`}}}}
      }}
    }},
    scales:{{
      x:{{ticks:{{color:"#e8eaf0"}},grid:{{color:"#1e2a3a"}}}},
      y:{{beginAtZero:true,max:100,ticks:{{color:"#e8eaf0",callback:v=>v+"%"}},grid:{{color:"#1e2a3a"}}}}
    }}
  }}
}});

// Conversion funnel chart: bar (взято в работу) + line (% конверсии)
new Chart(document.getElementById("convFunnelChart"),{{
  data:{{
    labels:DATA.conv_dates,
    datasets:[
      {{
        type:"bar",
        label:"Взято в работу",
        data:DATA.conv_vzv,
        backgroundColor:"#00cec9",
        borderRadius:3,
        yAxisID:"yCount",
        order:2,
      }},
      {{
        type:"line",
        label:"% в Контакт установлен",
        data:DATA.conv_pct,
        borderColor:"#ffd32a",
        backgroundColor:"transparent",
        pointBackgroundColor:"#ffd32a",
        pointRadius:4,
        tension:0.3,
        yAxisID:"yPct",
        order:1,
        datalabels:{{display:true}},
      }}
    ]
  }},
  options:{{
    maintainAspectRatio:false,
    interaction:{{mode:"index",intersect:false}},
    plugins:{{
      legend:{{labels:{{color:"#e8eaf0"}}}},
      tooltip:{{callbacks:{{
        label:function(c){{
          return c.dataset.yAxisID==="yPct"
            ? ` ${{c.dataset.label}}: ${{c.raw}}%`
            : ` ${{c.dataset.label}}: ${{c.raw}}`;
        }}
      }}}}
    }},
    scales:{{
      x:{{ticks:{{color:"#e8eaf0"}},grid:{{color:"#1e2a3a"}}}},
      yCount:{{
        position:"left",
        beginAtZero:true,
        ticks:{{color:"#e8eaf0"}},
        grid:{{color:"#1e2a3a"}},
        title:{{display:true,text:"Взято в работу",color:"#00cec9"}},
      }},
      yPct:{{
        position:"right",
        min:0,max:100,
        ticks:{{color:"#ffd32a",callback:v=>v+"%"}},
        grid:{{drawOnChartArea:false}},
        title:{{display:true,text:"Конверсия %",color:"#ffd32a"}},
      }}
    }}
  }}
}});

// Capital doughnut
new Chart(document.getElementById("capitalChart"),{{
  type:"doughnut",
  data:{{
    labels:DATA.capital_labels,
    datasets:[{{
      data:DATA.capital_values,
      backgroundColor:["#eb4d4b","#f5a623","#ffd32a","#6ab04c","#00cec9","#4f8ef7","#a29bfe","#636e72"],
      borderWidth:0,
    }}]
  }},
  options:{{
    maintainAspectRatio:false,
    plugins:{{
      legend:{{position:"right",labels:{{color:"#e8eaf0",font:{{size:12}},boxWidth:14,padding:10}}}},
      tooltip:{{callbacks:{{label:function(c){{
        const total=c.dataset.data.reduce((a,b)=>a+b,0);
        return ` ${{c.label}}: ${{c.raw}} (${{Math.round(c.raw/total*100)}}%)`;
      }}}}}}
    }}
  }}
}});

// Ready doughnut
new Chart(document.getElementById("readyChart"),{{
  type:"doughnut",
  data:{{
    labels:DATA.ready_labels.map(l=>l==="Супер_Я_готов"?"Готов сейчас":l==="Хочу_больше_узнать_про_программу"?"Хочу узнать больше":l),
    datasets:[{{
      data:DATA.ready_values,
      backgroundColor:["#6ab04c","#4f8ef7","#f5a623","#a29bfe"],
      borderWidth:0,
    }}]
  }},
  options:{{
    maintainAspectRatio:false,
    plugins:{{
      legend:{{position:"right",labels:{{color:"#e8eaf0",font:{{size:12}},boxWidth:14,padding:10}}}},
      tooltip:{{callbacks:{{label:function(c){{
        const total=c.dataset.data.reduce((a,b)=>a+b,0);
        return ` ${{c.label}}: ${{c.raw}} (${{Math.round(c.raw/total*100)}}%)`;
      }}}}}}
    }}
  }}
}});

// Table
const tbody=document.getElementById("mgrTable");
mgrIds.forEach(id=>{{
  const d=DATA.mgr_viz[id]||{{}};
  const tot=Object.values(d).reduce((s,v)=>s+v,0);
  const ov=DATA.overdue[id]||0;
  const ovColor=ov>20?"#eb4d4b":ov>5?"#f5a623":"#6ab04c";
  tbody.innerHTML+=`<tr>
    <td>${{DATA.managers[id]||id}}</td>
    <td class="num">${{fmt(tot)}}</td>
    <td class="num"><span class="tag tag-incoming">${{d.incoming||0}}</span></td>
    <td class="num"><span class="tag tag-new-lead">${{d.new_lead||0}}</span></td>
    <td class="num"><span class="tag tag-om">${{d.om||0}}</span></td>
    <td class="num"><span class="tag tag-in-work">${{d.in_work||0}}</span></td>
    <td class="num"><span class="tag tag-contact">${{d.contact||0}}</span></td>
    <td class="num"><span class="tag tag-qualified">${{d.qualified||0}}</span></td>
    <td class="num"><span class="tag tag-ndz">${{d.ndz||0}}</span></td>
    <td class="num"><span class="tag tag-offer">${{d.offer||0}}</span></td>
    <td class="num"><span class="tag tag-delayed">${{d.delayed||0}}</span></td>
    <td class="num"><span class="tag tag-sale">${{d.sale||0}}</span></td>
    <td class="num"><span class="tag tag-lost">${{d.lost||0}}</span></td>
    <td class="num" style="color:${{ovColor}}">${{ov}}</td>
  </tr>`;
}});
</script>
</body>
</html>
"""

def generate_html(report):
    gc = report["group_counts"]
    sales = gc.get("sale", 0)
    total = report["total"]
    conv_pct = round(sales / total * 100, 2) if total else 0
    price_fmt = f"{report['total_price']:,}".replace(",", "\u00a0")

    json_data = json.dumps({
        "sorted_statuses": report["sorted_statuses"],
        "group_counts":    report["group_counts"],
        "managers":        {str(k): v for k, v in report["managers"].items()},
        "mgr_viz":         report["mgr_viz"],
        "overdue":         report["overdue"],
        "daily_labels":    report["daily_labels"],
        "daily_values":    report["daily_values"],
        "capital_labels":  report["capital_labels"],
        "capital_values":  report["capital_values"],
        "daily_cap_labels": report["daily_cap_labels"],
        "daily_cap_data":   report["daily_cap_data"],
        "ready_labels":    report["ready_labels"],
        "ready_values":    report["ready_values"],
        "conv_dates":      report["conv_dates"],
        "conv_vzv":        report["conv_vzv"],
        "conv_pct":        report["conv_pct"],
    }, ensure_ascii=False)

    active_total = sum(gc.get(g, 0) for g in ("incoming", "new_lead", "om", "in_work", "contact", "qualified"))

    return HTML_TEMPLATE.format(
        updated_at = report["updated_at"],
        total      = f"{total:,}".replace(",", "\u00a0"),
        active     = active_total,
        ndz        = gc.get("ndz", 0),
        offer_ozv  = gc.get("offer", 0),
        delayed    = gc.get("delayed", 0),
        excursion  = gc.get("excursion", 0),
        invoiced   = gc.get("invoiced", 0),
        sales      = sales,
        conv_pct   = conv_pct,
        price      = price_fmt,
        json_data  = json_data,
    )

if __name__ == "__main__":
    report = build_report()
    html = generate_html(report)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done. Total: {report['total']}, Sales: {report['group_counts'].get('invoiced',0)+report['group_counts'].get('sale',0)}")
