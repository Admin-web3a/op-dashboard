#!/usr/bin/env python3
"""
amoCRM Daily ОП Dashboard Generator
Generates docs/index.html with Chart.js visualizations.
Run daily via GitHub Actions (cron 0 4 * * * = 7:00 MSK).
"""

import urllib.request
import urllib.parse
import json
import os
import datetime
from collections import Counter, defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN  = os.environ["AMO_TOKEN"]
DOMAIN = "simmihur.amocrm.ru"

SOURCE_FIELD_ID = 1321741   # Рабочий источник
SOURCE_ENUM_ID  = 953633    # Анкета перезаписи 06.2026
UPDATED_FROM    = 1743465600  # 2026-04-01 — нижняя граница updated_at

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

# Группировка статусов для цветового кодирования
STATUS_GROUPS = {
    "НДЗ": "ndz",
    "Входящий чекин": "active",
    "ОМ назначен чекин": "active",
    "Взято в работу": "active",
    "Контакт установлен": "active",
    "Новый лид": "active",
    "Квалифицирован": "active",
    "ом назначен": "active",
    "ОМ назначен": "active",
    "Отложенный спрос": "offer",
    "Оффер озвучен": "offer",
    "Выставлен счет": "won",
    "Внутренняя рассрочка": "won",
    "Экскурсия": "won",
    "Закрыто и не реализовано": "lost",
    "Успешно реализовано": "won",
}

GROUP_LABELS = {
    "active": "В работе",
    "ndz":    "НДЗ",
    "offer":  "На оффере",
    "won":    "Продажи",
    "lost":   "Потеряно",
}

GROUP_COLORS = {
    "active": "#4f8ef7",
    "ndz":    "#f5a623",
    "offer":  "#7ed6df",
    "won":    "#6ab04c",
    "lost":   "#eb4d4b",
}

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path: str) -> dict:
    url = f"https://{DOMAIN}/api/v4/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_all_pages(base_path: str, stop_fn=None) -> list:
    """Fetch paginated results, optionally stopping early via stop_fn(leads) -> bool."""
    results = []
    page = 1
    while True:
        sep = "&" if "?" in base_path else "?"
        data = api_get(f"{base_path}{sep}limit=250&page={page}")
        items = data.get("_embedded", {})
        # detect the key
        key = next(iter(items), None)
        batch = items.get(key, []) if key else []
        if not batch:
            break
        results.extend(batch)
        if stop_fn and stop_fn(batch):
            break
        if len(batch) < 250:
            break
        page += 1
    return results

# ── Fetch data ────────────────────────────────────────────────────────────────

def fetch_pipelines():
    data = api_get("leads/pipelines")
    statuses = {}
    for p in data.get("_embedded", {}).get("pipelines", []):
        for s in p.get("_embedded", {}).get("statuses", []):
            statuses[s["id"]] = {
                "name":     s["name"],
                "pipeline": p["name"],
                "group":    STATUS_GROUPS.get(s["name"], "active"),
            }
    return statuses


def fetch_filtered_leads(statuses: dict) -> list:
    """Fetch leads with SOURCE_ENUM_ID set, using updated_at order."""
    filtered = []
    consecutive_empty = 0
    page = 1
    while True:
        path = (
            f"leads?limit=250&page={page}"
            f"&order[updated_at]=desc"
            f"&filter[updated_at][from]={UPDATED_FROM}"
        )
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
        if matched == 0:
            consecutive_empty += 1
            if consecutive_empty >= 5:
                break
        else:
            consecutive_empty = 0
        if len(batch) < 250:
            break
        page += 1
    return filtered


def fetch_overdue_tasks() -> dict:
    """Returns {user_id: overdue_count} for all managers."""
    now_ts = int(datetime.datetime.utcnow().timestamp())
    path = f"tasks?filter[is_completed]=0&filter[complete_till][to]={now_ts}"
    try:
        tasks = fetch_all_pages(path)
    except Exception:
        return {}
    counts = Counter()
    for t in tasks:
        uid = t.get("responsible_user_id")
        if uid in MANAGERS:
            counts[uid] += 1
    return dict(counts)

# ── Build report data ─────────────────────────────────────────────────────────

def build_report():
    print("Fetching pipelines…")
    statuses = fetch_pipelines()

    print("Fetching leads…")
    leads = fetch_filtered_leads(statuses)
    total = len(leads)
    total_price = sum(l.get("price") or 0 for l in leads)

    # Status counts (all leads)
    status_counts: Counter = Counter()
    for lead in leads:
        status_counts[lead.get("status_id")] += 1

    # Group counts (all leads)
    group_counts: Counter = Counter()
    for sid, cnt in status_counts.items():
        group = statuses.get(sid, {}).get("group", "active")
        group_counts[group] += cnt

    # Per-manager breakdown (managers only)
    mgr_status: dict = defaultdict(Counter)   # {uid: {group: count}}
    for lead in leads:
        uid = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        sid = lead.get("status_id")
        group = statuses.get(sid, {}).get("group", "active")
        mgr_status[uid][group] += 1

    print("Fetching overdue tasks…")
    overdue = fetch_overdue_tasks()

    # Build sorted status list for chart
    sorted_statuses = []
    for sid, cnt in status_counts.most_common():
        name = statuses.get(sid, {}).get("name", f"?({sid})")
        group = statuses.get(sid, {}).get("group", "active")
        pipeline = statuses.get(sid, {}).get("pipeline", "")
        sorted_statuses.append({"name": name, "count": cnt, "group": group, "pipeline": pipeline})

    return {
        "updated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК"),
        "total": total,
        "total_price": total_price,
        "group_counts": dict(group_counts),
        "sorted_statuses": sorted_statuses,
        "managers": MANAGERS,
        "mgr_status": {str(uid): dict(cnts) for uid, cnts in mgr_status.items()},
        "overdue": {str(uid): cnt for uid, cnt in overdue.items()},
    }

# ── HTML generation ───────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ОП Dashboard — Анкета перезаписи 06.2026</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e8eaf0; --muted: #8b8fa8; --accent: #4f8ef7;
    --green: #6ab04c; --orange: #f5a623; --red: #eb4d4b; --blue: #7ed6df;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; padding: 24px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
  h2 {{ font-size: 15px; font-weight: 600; margin: 32px 0 14px; color: var(--text); }}
  .meta {{ color: var(--muted); font-size: 12px; margin-bottom: 28px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 8px; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .stat-value {{ font-size: 28px; font-weight: 700; line-height: 1; }}
  .stat-label {{ color: var(--muted); font-size: 11px; margin-top: 6px; text-transform: uppercase; letter-spacing: .04em; }}
  .stat.accent .stat-value {{ color: var(--accent); }}
  .stat.orange .stat-value {{ color: var(--orange); }}
  .stat.green  .stat-value {{ color: var(--green); }}
  .stat.blue   .stat-value {{ color: var(--blue); }}
  .chart-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 700px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  th {{ background: #22253a; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-top: 1px solid var(--border); }}
  tr:hover td {{ background: #1e2133; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
  .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .tag-active {{ background: #1a2a4a; color: var(--accent); }}
  .tag-ndz    {{ background: #3a2800; color: var(--orange); }}
  .tag-offer  {{ background: #0a2e30; color: var(--blue); }}
  .tag-won    {{ background: #1a2e0a; color: var(--green); }}
  .tag-lost   {{ background: #2e0a0a; color: var(--red); }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
<h1>ОП Dashboard — Анкета перезаписи 06.2026</h1>
<p class="meta">Источник: amoCRM simmihur &nbsp;·&nbsp; Обновлено: {updated_at}</p>

<div class="stats">
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Всего лидов</div></div>
  <div class="stat accent"><div class="stat-value">{active}</div><div class="stat-label">В работе</div></div>
  <div class="stat orange"><div class="stat-value">{ndz}</div><div class="stat-label">НДЗ</div></div>
  <div class="stat blue"><div class="stat-value">{offer}</div><div class="stat-label">На оффере</div></div>
  <div class="stat green"><div class="stat-value">{won}</div><div class="stat-label">Продажи</div></div>
  <div class="stat"><div class="stat-value">{conv_pct}%</div><div class="stat-label">Конверсия в оффер</div></div>
  <div class="stat"><div class="stat-value">{price}</div><div class="stat-label">Сумма сделок, ₽</div></div>
</div>

<h2>Распределение по статусам воронки</h2>
<div class="chart-card"><canvas id="funnelChart" height="320"></canvas></div>

<h2>Лиды по менеджерам</h2>
<div class="chart-card"><canvas id="mgrChart" height="260"></canvas></div>

<div class="grid2" style="margin-top:16px">
  <div>
    <h2>Просроченные задачи по менеджерам</h2>
    <div class="chart-card"><canvas id="overdueChart" height="220"></canvas></div>
  </div>
  <div>
    <h2>Конверсия по этапам</h2>
    <div class="chart-card"><canvas id="convChart" height="220"></canvas></div>
  </div>
</div>

<h2>Детализация по менеджерам</h2>
<table>
  <thead>
    <tr>
      <th>Менеджер</th>
      <th class="num">Всего</th>
      <th class="num">В работе</th>
      <th class="num">НДЗ</th>
      <th class="num">Оффер</th>
      <th class="num">Продажи</th>
      <th class="num">Потеряно</th>
      <th class="num">Просрочено</th>
    </tr>
  </thead>
  <tbody id="mgrTable"></tbody>
</table>

<script>
const DATA = {json_data};

// ── Helpers ──────────────────────────────────────────────────────────────────
const COLORS = {{
  active: "#4f8ef7", ndz: "#f5a623", offer: "#7ed6df", won: "#6ab04c", lost: "#eb4d4b"
}};
const LABELS = {{
  active: "В работе", ndz: "НДЗ", offer: "На оффере", won: "Продажи", lost: "Потеряно"
}};
const GROUPS = ["active","ndz","offer","won","lost"];

function fmt(n) {{ return (n||0).toLocaleString("ru-RU"); }}

const chartDefaults = {{
  responsive: true,
  plugins: {{
    legend: {{ labels: {{ color: "#8b8fa8", font: {{ size: 12 }} }} }},
    tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{fmt(ctx.raw)}}` }} }}
  }},
  scales: {{
    x: {{ ticks: {{ color: "#8b8fa8" }}, grid: {{ color: "#2a2d3a" }} }},
    y: {{ ticks: {{ color: "#8b8fa8" }}, grid: {{ color: "#2a2d3a" }} }}
  }}
}};

// ── Funnel chart (horizontal bars) ───────────────────────────────────────────
new Chart(document.getElementById("funnelChart"), {{
  type: "bar",
  data: {{
    labels: DATA.sorted_statuses.map(s => s.name),
    datasets: [{{
      label: "Сделок",
      data: DATA.sorted_statuses.map(s => s.count),
      backgroundColor: DATA.sorted_statuses.map(s => COLORS[s.group] || "#4f8ef7"),
      borderRadius: 3,
    }}]
  }},
  options: {{ ...chartDefaults, indexAxis: "y",
    plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }},
    scales: {{
      x: {{ ...chartDefaults.scales.x }},
      y: {{ ticks: {{ color: "#e8eaf0", font: {{ size: 12 }} }}, grid: {{ color: "#2a2d3a" }} }}
    }}
  }}
}});

// ── Manager stacked bar chart ────────────────────────────────────────────────
const mgrIds = Object.keys(DATA.mgr_status).sort((a,b) => {{
  const ta = Object.values(DATA.mgr_status[a]).reduce((s,v)=>s+v,0);
  const tb = Object.values(DATA.mgr_status[b]).reduce((s,v)=>s+v,0);
  return tb - ta;
}});
const mgrNames = mgrIds.map(id => DATA.managers[id] || id);

new Chart(document.getElementById("mgrChart"), {{
  type: "bar",
  data: {{
    labels: mgrNames,
    datasets: GROUPS.map(g => ({{
      label: LABELS[g],
      data: mgrIds.map(id => (DATA.mgr_status[id] || {{}})[g] || 0),
      backgroundColor: COLORS[g],
      borderRadius: 2,
    }}))
  }},
  options: {{ ...chartDefaults, scales: {{
    x: {{ ...chartDefaults.scales.x, stacked: true }},
    y: {{ ...chartDefaults.scales.y, stacked: true }}
  }}}}
}});

// ── Overdue tasks chart ───────────────────────────────────────────────────────
const overdueIds = Object.keys(DATA.overdue).sort((a,b) => DATA.overdue[b]-DATA.overdue[a]);
new Chart(document.getElementById("overdueChart"), {{
  type: "bar",
  data: {{
    labels: overdueIds.map(id => DATA.managers[id] || id),
    datasets: [{{
      label: "Просрочено",
      data: overdueIds.map(id => DATA.overdue[id]),
      backgroundColor: "#eb4d4b",
      borderRadius: 3,
    }}]
  }},
  options: {{ ...chartDefaults,
    plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }}
  }}
}});

// ── Conversion funnel chart ───────────────────────────────────────────────────
const convStages = ["В работе","НДЗ","На оффере","Продажи"];
const convData   = [
  DATA.group_counts.active||0,
  DATA.group_counts.ndz||0,
  DATA.group_counts.offer||0,
  DATA.group_counts.won||0,
];
new Chart(document.getElementById("convChart"), {{
  type: "bar",
  data: {{
    labels: convStages,
    datasets: [{{
      label: "Лидов",
      data: convData,
      backgroundColor: [COLORS.active, COLORS.ndz, COLORS.offer, COLORS.won],
      borderRadius: 3,
    }}]
  }},
  options: {{ ...chartDefaults,
    plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }}
  }}
}});

// ── Manager table ─────────────────────────────────────────────────────────────
const tbody = document.getElementById("mgrTable");
mgrIds.forEach(id => {{
  const d = DATA.mgr_status[id] || {{}};
  const tot = Object.values(d).reduce((s,v)=>s+v,0);
  const ov  = DATA.overdue[id] || 0;
  const row = `<tr>
    <td>${{DATA.managers[id] || id}}</td>
    <td class="num">${{fmt(tot)}}</td>
    <td class="num"><span class="tag tag-active">${{d.active||0}}</span></td>
    <td class="num"><span class="tag tag-ndz">${{d.ndz||0}}</span></td>
    <td class="num"><span class="tag tag-offer">${{d.offer||0}}</span></td>
    <td class="num"><span class="tag tag-won">${{d.won||0}}</span></td>
    <td class="num"><span class="tag tag-lost">${{d.lost||0}}</span></td>
    <td class="num" style="color:${{ov>10?'#eb4d4b':ov>0?'#f5a623':'#6ab04c'}}">${{ov}}</td>
  </tr>`;
  tbody.innerHTML += row;
}});
</script>
</body>
</html>
"""

def generate_html(report: dict) -> str:
    gc = report["group_counts"]
    offer_total = (gc.get("offer", 0) + gc.get("won", 0))
    conv_pct = round(offer_total / report["total"] * 100, 1) if report["total"] else 0
    price_fmt = f"{report['total_price']:,}".replace(",", " ")

    json_data = json.dumps({
        "sorted_statuses": report["sorted_statuses"],
        "group_counts":    report["group_counts"],
        "managers":        {str(k): v for k, v in report["managers"].items()},
        "mgr_status":      report["mgr_status"],
        "overdue":         report["overdue"],
    }, ensure_ascii=False)

    return HTML_TEMPLATE.format(
        updated_at = report["updated_at"],
        total      = f"{report['total']:,}".replace(",", " "),
        active     = gc.get("active", 0),
        ndz        = gc.get("ndz", 0),
        offer      = offer_total,
        won        = gc.get("won", 0),
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
    print(f"Done. Total leads: {report['total']}")
