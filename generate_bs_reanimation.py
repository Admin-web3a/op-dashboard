#!/usr/bin/env python3
"""
БС-Реанимация Dashboard Generator
Generates docs/bs-reanimation.html

Scope: tag «быстрыйстартреанимация» (id 244287)
       + Основная воронка ОП
       + updated_at >= 2026-08-01 (proxy for «tag applied in August»)
"""

import urllib.request
import urllib.parse
import json
import os
import datetime
import time
from collections import Counter, defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN  = os.environ["AMO_TOKEN"]
DOMAIN = "simmihur.amocrm.ru"

TAG_ID       = 244287
UPDATED_FROM = 1785542400  # 2026-08-01 00:00 UTC
PIPELINE_NAME = "Основная воронка ОП"

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
     6976552: "Виолетта Осадчук",
}

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

VIZ_ORDER  = ["in_work", "contact", "qualified", "ndz", "offer", "delayed", "invoiced", "excursion", "sale", "lost"]
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
    "invoiced":  "Выставлен счёт",
    "excursion": "Экскурсия",
    "sale":      "Продажа",
    "lost":      "Закрыто",
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
    "invoiced":  "#fd79a8",
    "excursion": "#55efc4",
    "sale":      "#6ab04c",
    "lost":      "#eb4d4b",
}

# Кумулятивная воронка: каждый этап = лиды, дошедшие до него ИЛИ дальше
ATTR_FUNNEL = [
    ("Взято в работу",     {"in_work", "contact", "qualified", "ndz", "offer", "delayed", "invoiced", "excursion", "sale"}),
    ("Контакт установлен", {"contact", "qualified", "offer", "delayed", "invoiced", "excursion", "sale"}),
    ("Квалифицирован",     {"qualified", "offer", "delayed", "invoiced", "excursion", "sale"}),
    ("Оффер озвучен",      {"offer", "delayed", "invoiced", "excursion", "sale"}),
    ("Продажа",            {"sale"}),
]

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path, params=None):
    url = f"https://{DOMAIN}/api/v4/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status == 204:
            return None
        return json.loads(r.read())


def paginate(path, base_params, limit=50, sleep=0.2):
    """Yield all items from a paginated endpoint."""
    page = 1
    while True:
        params = {**base_params, "limit": limit, "page": page}
        data = api_get(path, params)
        if data is None:
            break
        items = []
        for key in ("leads", "tasks"):
            items = data.get("_embedded", {}).get(key, [])
            if items:
                break
        if not items:
            break
        yield from items
        if len(items) < limit:
            break
        page += 1
        if sleep:
            time.sleep(sleep)

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_pipelines():
    """Return (status_map, pipeline_id) where status_map: {status_id: {name, group}}."""
    data = api_get("leads/pipelines")
    status_map = {}
    pipeline_id = None
    for p in data.get("_embedded", {}).get("pipelines", []):
        if p["name"] == PIPELINE_NAME:
            pipeline_id = p["id"]
        for s in p.get("_embedded", {}).get("statuses", []):
            name = s["name"]
            status_map[s["id"]] = {
                "name":     name,
                "pipeline": p["name"],
                "group":    STATUS_GROUPS.get(name, "in_work"),
            }
    return status_map, pipeline_id


def fetch_leads(pipeline_id):
    params = {
        "filter[tag_id][]":         TAG_ID,
        "filter[pipeline_id][]":    pipeline_id,
        "filter[updated_at][from]": UPDATED_FROM,
    }
    leads = list(paginate("leads", params, limit=50, sleep=0.25))
    print(f"  Загружено {len(leads)} лидов")
    return leads


def fetch_overdue_tasks(lead_ids):
    """Просроченные задачи (is_completed=0, complete_till < now) по нашим лидам."""
    now_ts = int(datetime.datetime.utcnow().timestamp())
    counts = Counter()
    for task in paginate("tasks", {
        "filter[is_completed]": 0,
        "filter[complete_till][to]": now_ts,
    }, limit=250, sleep=0.1):
        if task.get("entity_type") == "leads" and task.get("entity_id") in lead_ids:
            uid = task.get("responsible_user_id")
            if uid in MANAGERS:
                counts[uid] += 1
    return counts


def fetch_active_task_lead_ids(lead_ids):
    """Вернуть множество lead_id, у которых есть хотя бы одна открытая задача."""
    with_task = set()
    for task in paginate("tasks", {
        "filter[is_completed]": 0,
    }, limit=250, sleep=0.1):
        eid = task.get("entity_id")
        if task.get("entity_type") == "leads" and eid in lead_ids:
            with_task.add(eid)
    return with_task

# ── Compute ───────────────────────────────────────────────────────────────────

def compute_daily(leads):
    """Группировка по дням updated_at (МСК), от 1 августа."""
    tz_msk = datetime.timezone(datetime.timedelta(hours=3))
    start  = datetime.date(2026, 8, 1)
    today  = datetime.datetime.now(tz=tz_msk).date()
    counts = Counter()
    for lead in leads:
        ts   = lead.get("updated_at", 0)
        day  = datetime.datetime.fromtimestamp(ts, tz=tz_msk).date()
        if start <= day <= today:
            counts[day] += 1
    days   = sorted(counts)
    labels = [d.strftime("%-d %b") for d in days]
    values = [counts[d] for d in days]
    return labels, values


def compute_mgr_stacks(leads, statuses):
    """Stacked data by manager × VIZ_ORDER groups."""
    mgr_counts = defaultdict(Counter)
    for lead in leads:
        uid   = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        group = statuses.get(lead.get("status_id"), {}).get("group", "in_work")
        mgr_counts[uid][group] += 1

    mgr_labels = [MANAGERS[uid] for uid in MANAGERS if uid in mgr_counts]
    mgr_ids    = [uid for uid in MANAGERS if uid in mgr_counts]
    datasets   = []
    for vg in VIZ_ORDER:
        values = [mgr_counts[uid].get(vg, 0) for uid in mgr_ids]
        if any(v > 0 for v in values):
            datasets.append({
                "label":           VIZ_LABELS.get(vg, vg),
                "data":            values,
                "backgroundColor": VIZ_COLORS.get(vg, "#999"),
            })
    return mgr_labels, datasets


def compute_no_task_by_manager(leads, leads_with_task):
    """Кол-во лидов БЕЗ активной задачи по менеджерам."""
    counts = Counter()
    for lead in leads:
        if lead["id"] not in leads_with_task:
            uid = lead.get("responsible_user_id")
            if uid in MANAGERS:
                counts[uid] += 1
    return counts


def compute_cumulative_funnel(leads, statuses):
    """Атрибутивная воронка: сколько лидов дошло до каждого этапа."""
    groups = [statuses.get(l.get("status_id"), {}).get("group", "in_work") for l in leads]
    return [
        {"name": name, "count": sum(1 for g in groups if g in stage_groups)}
        for name, stage_groups in ATTR_FUNNEL
    ]

# ── HTML ──────────────────────────────────────────────────────────────────────

def generate_html(report):
    now_str       = report["updated_at"]
    daily_labels  = json.dumps(report["daily_labels"],  ensure_ascii=False)
    daily_values  = json.dumps(report["daily_values"])
    mgr_labels    = json.dumps(report["mgr_labels"],    ensure_ascii=False)
    mgr_datasets  = json.dumps(report["mgr_datasets"],  ensure_ascii=False)
    overdue_mgrs  = json.dumps(report["overdue_mgrs"],  ensure_ascii=False)
    overdue_vals  = json.dumps(report["overdue_vals"])
    notask_mgrs   = json.dumps(report["notask_mgrs"],   ensure_ascii=False)
    notask_vals   = json.dumps(report["notask_vals"])
    funnel_labels = json.dumps(report["funnel_labels"], ensure_ascii=False)
    funnel_values = json.dumps(report["funnel_values"])
    total         = report["total"]
    sold          = report["sold"]
    in_work       = report["in_work"]
    lost          = report["lost"]
    no_task_total = report["no_task_total"]
    overdue_total = report["overdue_total"]

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>БС Реанимация — ОП Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
  :root{{--bg:#0f172a;--surface:#1e293b;--border:#334155;--text:#f1f5f9;--muted:#94a3b8;--accent:#38bdf8}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px}}
  h1{{font-size:1.4rem;font-weight:700;margin-bottom:4px}}
  .meta{{color:var(--muted);font-size:.85rem;margin-bottom:20px}}
  .meta a{{color:var(--accent);text-decoration:none}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px}}
  .stat{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
  .stat-val{{font-size:1.8rem;font-weight:700;line-height:1.1}}
  .stat-lbl{{font-size:.8rem;color:var(--muted);margin-top:4px}}
  .stat-val.green{{color:#6ab04c}}.stat-val.red{{color:#eb4d4b}}.stat-val.yellow{{color:#f5a623}}.stat-val.blue{{color:#38bdf8}}
  h2{{font-size:1rem;font-weight:600;margin:24px 0 10px}}
  .chart-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  @media(max-width:768px){{.grid2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>БС Реанимация — Основная воронка ОП</h1>
<div class="meta">Тег: быстрыйстартреанимация &nbsp;·&nbsp; Обновлено: {now_str} &nbsp;·&nbsp;
  <a href="/op-dashboard/">← op-dashboard</a></div>

<div class="stats">
  <div class="stat"><div class="stat-val blue">{total}</div><div class="stat-lbl">Всего лидов</div></div>
  <div class="stat"><div class="stat-val green">{sold}</div><div class="stat-lbl">Продажи</div></div>
  <div class="stat"><div class="stat-val">{in_work}</div><div class="stat-lbl">В работе</div></div>
  <div class="stat"><div class="stat-val red">{lost}</div><div class="stat-lbl">Закрыто</div></div>
  <div class="stat"><div class="stat-val yellow">{no_task_total}</div><div class="stat-lbl">Без задачи</div></div>
  <div class="stat"><div class="stat-val red">{overdue_total}</div><div class="stat-lbl">Просрочено задач</div></div>
</div>

<h2>Лиды по дням (дата тега)</h2>
<div class="chart-card" style="height:220px"><canvas id="dailyChart"></canvas></div>

<h2>Лиды по менеджерам</h2>
<div class="chart-card" style="height:500px"><canvas id="mgrChart"></canvas></div>

<h2>Кумулятивная воронка</h2>
<div class="chart-card" style="height:320px"><canvas id="funnelChart"></canvas></div>

<h2>Контроль качества по менеджерам</h2>
<div class="grid2">
  <div>
    <p style="font-size:.85rem;color:var(--muted);margin-bottom:8px">Без активной задачи</p>
    <div class="chart-card" style="height:320px"><canvas id="noTaskChart"></canvas></div>
  </div>
  <div>
    <p style="font-size:.85rem;color:var(--muted);margin-bottom:8px">Просроченные задачи</p>
    <div class="chart-card" style="height:320px"><canvas id="overdueChart"></canvas></div>
  </div>
</div>

<script>
if(typeof ChartDataLabels !== 'undefined') Chart.unregister(ChartDataLabels);

const DARK = {{
  color: '#f1f5f9',
  borderColor: '#334155',
  grid: {{ color: 'rgba(255,255,255,.06)' }},
}};
const base = {{
  responsive: true,
  maintainAspectRatio: false,
  plugins: {{
    legend: {{ labels: {{ color: DARK.color, boxWidth: 12, font: {{ size: 12 }} }} }},
    tooltip: {{ mode: 'index', intersect: false }},
  }},
  scales: {{
    x: {{ ticks: {{ color: DARK.color }}, grid: DARK.grid }},
    y: {{ ticks: {{ color: DARK.color }}, grid: DARK.grid }},
  }},
}};

// 1. Daily chart
new Chart(document.getElementById('dailyChart'), {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{ label: 'Лиды', data: {daily_values},
      backgroundColor: 'rgba(56,189,248,.7)', borderColor: '#38bdf8',
      borderWidth: 1, borderRadius: 3 }}]
  }},
  options: {{...base,
    plugins: {{...base.plugins, legend: {{ display: false }} }},
    scales: {{ x: {{ ...base.scales.x, ticks: {{ ...base.scales.x.ticks, maxRotation: 45, font: {{ size: 11 }} }} }},
               y: {{ ...base.scales.y }} }}
  }},
}});

// 2. Manager stacked chart
new Chart(document.getElementById('mgrChart'), {{
  type: 'bar',
  data: {{ labels: {mgr_labels}, datasets: {mgr_datasets} }},
  options: {{...base,
    indexAxis: 'y',
    scales: {{
      x: {{ ...base.scales.x, stacked: true }},
      y: {{ ...base.scales.y, stacked: true, ticks: {{ color: DARK.color }} }},
    }},
  }},
}});

// 3. Cumulative funnel
new Chart(document.getElementById('funnelChart'), {{
  type: 'bar',
  data: {{
    labels: {funnel_labels},
    datasets: [{{
      label: 'Лидов',
      data: {funnel_values},
      backgroundColor: ['#00cec9','#ffd32a','#ff6b81','#7ed6df','#6ab04c'],
      borderRadius: 4,
    }}]
  }},
  options: {{...base,
    indexAxis: 'y',
    plugins: {{
      ...base.plugins,
      legend: {{ display: false }},
      datalabels: {{ anchor: 'end', align: 'right', color: '#f1f5f9', font: {{ size: 12, weight: 'bold' }} }},
    }},
    scales: {{ x: {{ ...base.scales.x }}, y: {{ ...base.scales.y }} }},
  }},
  plugins: [ChartDataLabels],
}});

// 4. No-task chart
new Chart(document.getElementById('noTaskChart'), {{
  type: 'bar',
  data: {{
    labels: {notask_mgrs},
    datasets: [{{ label: 'Без задачи', data: {notask_vals},
      backgroundColor: 'rgba(245,166,35,.8)', borderRadius: 3 }}]
  }},
  options: {{...base,
    indexAxis: 'y',
    plugins: {{...base.plugins, legend: {{ display: false }}}},
    scales: {{ x: {{ ...base.scales.x }}, y: {{ ...base.scales.y }} }},
  }},
}});

// 5. Overdue tasks chart
new Chart(document.getElementById('overdueChart'), {{
  type: 'bar',
  data: {{
    labels: {overdue_mgrs},
    datasets: [{{ label: 'Просрочено', data: {overdue_vals},
      backgroundColor: 'rgba(235,77,75,.8)', borderRadius: 3 }}]
  }},
  options: {{...base,
    indexAxis: 'y',
    plugins: {{...base.plugins, legend: {{ display: false }}}},
    scales: {{ x: {{ ...base.scales.x }}, y: {{ ...base.scales.y }} }},
  }},
}});
</script>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────

def build_report():
    tz_msk = datetime.timezone(datetime.timedelta(hours=3))
    now    = datetime.datetime.now(tz=tz_msk)

    print("Fetching pipelines…")
    statuses, pipeline_id = fetch_pipelines()
    if not pipeline_id:
        raise RuntimeError(f"Pipeline '{PIPELINE_NAME}' not found")
    print(f"  Pipeline ID: {pipeline_id}")

    print("Fetching leads…")
    leads = fetch_leads(pipeline_id)

    lead_ids = {l["id"] for l in leads}

    print("Fetching overdue tasks…")
    overdue = fetch_overdue_tasks(lead_ids)

    print("Fetching all active tasks (for no-task detection)…")
    leads_with_task = fetch_active_task_lead_ids(lead_ids)
    print(f"  {len(leads_with_task)} leads have at least 1 active task")

    # Daily
    daily_labels, daily_values = compute_daily(leads)

    # Manager stacks
    mgr_labels, mgr_datasets = compute_mgr_stacks(leads, statuses)

    # No-task by manager
    no_task = compute_no_task_by_manager(leads, leads_with_task)
    notask_sorted  = sorted(no_task.items(), key=lambda x: -x[1])
    notask_mgrs    = [MANAGERS.get(uid, str(uid)) for uid, _ in notask_sorted]
    notask_vals    = [v for _, v in notask_sorted]

    # Overdue by manager
    overdue_sorted = sorted(overdue.items(), key=lambda x: -x[1])
    overdue_mgrs   = [MANAGERS.get(uid, str(uid)) for uid, _ in overdue_sorted]
    overdue_vals   = [v for _, v in overdue_sorted]

    # Cumulative funnel
    funnel = compute_cumulative_funnel(leads, statuses)
    funnel_labels = [f["name"] for f in funnel]
    funnel_values = [f["count"] for f in funnel]

    # Summary stats
    all_groups = [statuses.get(l.get("status_id"), {}).get("group", "in_work") for l in leads]
    sold     = sum(1 for g in all_groups if g == "sale")
    lost     = sum(1 for g in all_groups if g == "lost")
    in_work  = sum(1 for g in all_groups if g not in ("sale", "lost"))

    report = {
        "updated_at":    now.strftime("%d.%m.%Y %H:%M МСК"),
        "total":         len(leads),
        "sold":          sold,
        "in_work":       in_work,
        "lost":          lost,
        "no_task_total": sum(no_task.values()),
        "overdue_total": sum(overdue.values()),
        "daily_labels":  daily_labels,
        "daily_values":  daily_values,
        "mgr_labels":    mgr_labels,
        "mgr_datasets":  mgr_datasets,
        "notask_mgrs":   notask_mgrs,
        "notask_vals":   notask_vals,
        "overdue_mgrs":  overdue_mgrs,
        "overdue_vals":  overdue_vals,
        "funnel_labels": funnel_labels,
        "funnel_values": funnel_values,
    }

    html = generate_html(report)
    os.makedirs("docs", exist_ok=True)
    with open("docs/bs-reanimation.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved docs/bs-reanimation.html  ({len(leads)} leads, {sold} sold)")


if __name__ == "__main__":
    build_report()
