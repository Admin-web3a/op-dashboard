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
    "Входящий чекин":            "active",
    "ОМ назначен чекин":         "active",
    "Взято в работу":            "active",
    "Контакт установлен":        "active",
    "Новый лид":                 "active",
    "Квалифицирован":            "active",
    "ом назначен":               "active",
    "ОМ назначен":               "active",
    "Оффер озвучен":             "offer",
    "Отложенный спрос":          "delayed",
    "Выставлен счет":            "invoiced",
    "Экскурсия":                 "excursion",
    "Внутренняя рассрочка":      "installment",
    "Успешно реализовано":       "sale",
    "Закрыто и не реализовано":  "lost",
}

# Для менеджерских стэк-баров объединяем в 5 визуальных групп
VIZ_GROUP = {
    "active":      "active",
    "ndz":         "ndz",
    "offer":       "offer",
    "delayed":     "offer",
    "invoiced":    "sale",
    "excursion":   "sale",
    "installment": "sale",
    "sale":        "sale",
    "lost":        "lost",
}

VIZ_LABELS = {"active": "В работе", "ndz": "НДЗ", "offer": "Оффер/Отложен", "sale": "Продажи+", "lost": "Потеряно"}
VIZ_COLORS = {"active": "#4f8ef7", "ndz": "#f5a623", "offer": "#7ed6df", "sale": "#6ab04c", "lost": "#eb4d4b"}
VIZ_ORDER  = ["active", "ndz", "offer", "sale", "lost"]

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

    # Sorted status list for funnel chart
    sorted_statuses = []
    for sid, cnt in status_counts.most_common():
        info = statuses.get(sid, {})
        sorted_statuses.append({
            "name":     info.get("name", f"?({sid})"),
            "count":    cnt,
            "group":    info.get("group", "active"),
            "pipeline": info.get("pipeline", ""),
        })

    return {
        "updated_at":       datetime.datetime.now(
                                datetime.timezone(datetime.timedelta(hours=3))
                            ).strftime("%d.%m.%Y %H:%M МСК"),
        "total":            total,
        "total_price":      total_price,
        "group_counts":     dict(group_counts),
        "sorted_statuses":  sorted_statuses,
        "managers":         MANAGERS,
        "mgr_viz":          {str(uid): dict(cnts) for uid, cnts in mgr_viz.items()},
        "overdue":          {str(uid): cnt for uid, cnt in overdue.items()},
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
  .tag-active{{background:#1a2a4a;color:var(--accent)}}
  .tag-ndz{{background:#3a2800;color:var(--orange)}}
  .tag-offer{{background:#0a2e30;color:var(--blue)}}
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

<h2>Распределение по статусам воронки</h2>
<div class="chart-card"><canvas id="funnelChart" height="340"></canvas></div>

<h2>Лиды по менеджерам</h2>
<div class="chart-card"><canvas id="mgrChart" height="260"></canvas></div>

<div class="grid2" style="margin-top:16px">
  <div>
    <h2>Просроченные задачи по менеджерам</h2>
    <div class="chart-card"><canvas id="overdueChart" height="240"></canvas></div>
  </div>
  <div>
    <h2>Воронка по группам</h2>
    <div class="chart-card"><canvas id="convChart" height="240"></canvas></div>
  </div>
</div>

<h2>Детализация по менеджерам</h2>
<table>
  <thead><tr>
    <th>Менеджер</th>
    <th class="num">Всего</th>
    <th class="num">В работе</th>
    <th class="num">НДЗ</th>
    <th class="num">Оффер/Отл.</th>
    <th class="num">Продажи+</th>
    <th class="num">Потеряно</th>
    <th class="num">Просрочено</th>
  </tr></thead>
  <tbody id="mgrTable"></tbody>
</table>

<script>
const DATA = {json_data};
const VCOLORS = {{active:"#4f8ef7",ndz:"#f5a623",offer:"#7ed6df",sale:"#6ab04c",lost:"#eb4d4b"}};
const VLABELS = {{active:"В работе",ndz:"НДЗ",offer:"Оффер/Отложен",sale:"Продажи+",lost:"Потеряно"}};
const VORDER  = ["active","ndz","offer","sale","lost"];
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
  options:{{...base,indexAxis:"y",
    plugins:{{...base.plugins,legend:{{display:false}}}},
    scales:{{x:{{...base.scales.x}},y:{{ticks:{{color:"#e8eaf0",font:{{size:12}}}},grid:{{color:"#2a2d3a"}}}}}}
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
  options:{{...base,scales:{{
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
  options:{{...base,plugins:{{...base.plugins,legend:{{display:false}}}}}}
}});

// Conversion funnel
const gc=DATA.group_counts;
const convLabels=["В работе","НДЗ","Оффер озвучен","Отл. спрос","Экскурсия","Выст. счет","Продажи"];
const convVals=[gc.active||0,gc.ndz||0,gc.offer||0,gc.delayed||0,gc.excursion||0,gc.invoiced||0,gc.sale||0];
const convColors=["#4f8ef7","#f5a623","#7ed6df","#5dade2","#a29bfe","#a29bfe","#6ab04c"];
new Chart(document.getElementById("convChart"),{{
  type:"bar",
  data:{{
    labels:convLabels,
    datasets:[{{label:"Лидов",data:convVals,backgroundColor:convColors,borderRadius:3}}]
  }},
  options:{{...base,plugins:{{...base.plugins,legend:{{display:false}}}}}}
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
    <td class="num"><span class="tag tag-active">${{d.active||0}}</span></td>
    <td class="num"><span class="tag tag-ndz">${{d.ndz||0}}</span></td>
    <td class="num"><span class="tag tag-offer">${{d.offer||0}}</span></td>
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
    sales = (gc.get("invoiced", 0) + gc.get("sale", 0))
    total = report["total"]
    conv_pct = round(sales / total * 100, 2) if total else 0
    price_fmt = f"{report['total_price']:,}".replace(",", "\u00a0")

    json_data = json.dumps({
        "sorted_statuses": report["sorted_statuses"],
        "group_counts":    report["group_counts"],
        "managers":        {str(k): v for k, v in report["managers"].items()},
        "mgr_viz":         report["mgr_viz"],
        "overdue":         report["overdue"],
    }, ensure_ascii=False)

    return HTML_TEMPLATE.format(
        updated_at = report["updated_at"],
        total      = f"{total:,}".replace(",", "\u00a0"),
        active     = gc.get("active", 0),
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
