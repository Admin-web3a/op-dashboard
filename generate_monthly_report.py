#!/usr/bin/env python3
"""
amoCRM Monthly ОП Dashboard Generator
Generates docs/index.html — all deals from «Основная воронка ОП».
Client-side date filtering (by created_at or closed_at/payment_date).
Run daily via GitHub Actions.
"""

import urllib.request
import json
import os
import datetime
from collections import Counter, defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN       = os.environ["AMO_TOKEN"]
DOMAIN      = "simmihur.amocrm.ru"
PIPELINE_ID = 9826550        # Основная воронка ОП
FETCH_FROM  = 1751328000     # 2026-07-01 00:00 UTC — start of first tracked month

# Known custom field IDs (reused from launch dashboard)
CAPITAL_FIELD_ID   = 1304047
READY_FIELD_ID     = 1317111
REASON_FIELD_ID    = 180637
PAYMENT_DATE_NAME  = "Дата оплаты"   # will be resolved at runtime

TEST_REASON = "ТЕСТ"

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
     7728454: "Виктория Шинкарева",
     6976552: "Виолетта Осадчук",
     9596454: "Ковалева Любовь",
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

# ── API ───────────────────────────────────────────────────────────────────────

def api_get(path):
    url = f"https://{DOMAIN}/api/v4/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read()
        if not body.strip():
            return {}
        return json.loads(body)

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


def resolve_payment_date_field_id():
    """Find the field_id for 'Дата оплаты' custom field."""
    page = 1
    while page <= 5:
        try:
            data = api_get(f"leads/custom_fields?limit=50&page={page}")
            fields = data.get("_embedded", {}).get("custom_fields", [])
            if not fields:
                break
            for f in fields:
                if f.get("name") == PAYMENT_DATE_NAME:
                    fid = f["id"]
                    print(f"  Resolved '{PAYMENT_DATE_NAME}' → field_id {fid}")
                    return fid
            if len(fields) < 50:
                break
            page += 1
        except Exception as e:
            print(f"  Warning resolving payment_date field: {e}")
            break
    print(f"  '{PAYMENT_DATE_NAME}' field not found — will use closed_at only")
    return None


def fetch_pipeline_leads(statuses, payment_date_fid):
    """Fetch all leads from PIPELINE_ID updated since FETCH_FROM.
    Returns list of compact dicts with fields needed for client-side rendering."""
    leads = []
    page = 1
    MAX_PAGES = 40
    print(f"  Fetching pipeline leads (pipeline={PIPELINE_ID}, from={FETCH_FROM})…")
    while page <= MAX_PAGES:
        path = (f"leads?limit=250&page={page}&with=contacts"
                f"&filter[pipeline_id]={PIPELINE_ID}"
                f"&filter[updated_at][from]={FETCH_FROM}"
                f"&order[updated_at]=desc")
        try:
            data = api_get(path)
        except Exception as e:
            print(f"  Warning fetching page {page}: {e}")
            break
        batch = data.get("_embedded", {}).get("leads", [])
        if not batch:
            break
        leads.extend(batch)
        print(f"    page {page}: +{len(batch)} (total {len(leads)})")
        if len(batch) < 250:
            break
        page += 1
    return leads


def fetch_overdue_tasks_per_lead(lead_ids_set):
    """Returns {lead_id: overdue_task_count} for leads in lead_ids_set."""
    now_ts = int(datetime.datetime.utcnow().timestamp())
    tasks = []
    page = 1
    try:
        while True:
            path = (f"tasks?limit=250&page={page}"
                    f"&filter[is_completed]=0&filter[complete_till][to]={now_ts}")
            data = api_get(path)
            batch = data.get("_embedded", {}).get("tasks", [])
            if not batch:
                break
            tasks.extend(batch)
            if len(batch) < 250:
                break
            page += 1
    except Exception as e:
        print(f"  Warning fetching tasks: {e}")
    counts = Counter()
    for t in tasks:
        if t.get("entity_type") == "leads":
            eid = t.get("entity_id")
            if eid in lead_ids_set:
                counts[eid] += 1
    return dict(counts)


# ── Phone → Country ──────────────────────────────────────────────────────────

PHONE_COUNTRIES = [
    ("7700","Казахстан"),("7701","Казахстан"),("7702","Казахстан"),
    ("7705","Казахстан"),("7706","Казахстан"),("7707","Казахстан"),
    ("7708","Казахстан"),("7709","Казахстан"),("7710","Казахстан"),
    ("7711","Казахстан"),("7712","Казахстан"),("7713","Казахстан"),
    ("7714","Казахстан"),("7715","Казахстан"),("7716","Казахстан"),
    ("7717","Казахстан"),("7718","Казахстан"),("7719","Казахстан"),
    ("7721","Казахстан"),("7722","Казахстан"),("7723","Казахстан"),
    ("7724","Казахстан"),("7725","Казахстан"),("7726","Казахстан"),
    ("7727","Казахстан"),("7728","Казахстан"),("7729","Казахстан"),
    ("7","Россия"),("380","Украина"),("375","Беларусь"),("374","Армения"),
    ("994","Азербайджан"),("998","Узбекистан"),("996","Кыргызстан"),
    ("992","Таджикистан"),("993","Туркменистан"),("995","Грузия"),("373","Молдова"),
    ("972","Израиль"),("971","ОАЭ"),("90","Турция"),("49","Германия"),
    ("44","Великобритания"),("33","Франция"),("39","Италия"),("34","Испания"),
    ("48","Польша"),("420","Чехия"),("36","Венгрия"),("40","Румыния"),
    ("31","Нидерланды"),("32","Бельгия"),("41","Швейцария"),("43","Австрия"),
    ("46","Швеция"),("47","Норвегия"),("45","Дания"),("358","Финляндия"),
    ("352","Люксембург"),("386","Словения"),("385","Хорватия"),("381","Сербия"),
    ("370","Литва"),("371","Латвия"),("372","Эстония"),
    ("966","Саудовская Аравия"),("974","Катар"),("965","Кувейт"),
    ("62","Индонезия"),("60","Малайзия"),("65","Сингапур"),("66","Таиланд"),
    ("84","Вьетнам"),("82","Южная Корея"),("81","Япония"),("86","Китай"),("91","Индия"),
    ("1204","Канада"),("1226","Канада"),("1236","Канада"),("1249","Канада"),
    ("1250","Канада"),("1289","Канада"),("1306","Канада"),("1343","Канада"),
    ("1354","Канада"),("1365","Канада"),("1367","Канада"),("1368","Канада"),
    ("1403","Канада"),("1416","Канада"),("1418","Канада"),("1431","Канада"),
    ("1437","Канада"),("1438","Канада"),("1450","Канада"),("1468","Канада"),
    ("1506","Канада"),("1514","Канада"),("1519","Канада"),("1548","Канада"),
    ("1579","Канада"),("1581","Канада"),("1587","Канада"),("1604","Канада"),
    ("1613","Канада"),("1639","Канада"),("1647","Канада"),("1672","Канада"),
    ("1705","Канада"),("1709","Канада"),("1742","Канада"),("1778","Канада"),
    ("1780","Канада"),("1782","Канада"),("1807","Канада"),("1819","Канада"),
    ("1825","Канада"),("1867","Канада"),("1873","Канада"),("1902","Канада"),
    ("1905","Канада"),
    ("1","США"),("55","Бразилия"),("52","Мексика"),("54","Аргентина"),("61","Австралия"),
]

def phone_to_country(raw_phone):
    digits = "".join(c for c in raw_phone if c.isdigit()).lstrip("0") or ""
    for prefix, country in PHONE_COUNTRIES:
        if digits.startswith(prefix):
            return country
    return "Не определено"


def fetch_contacts_phones(contact_ids):
    """Batch-fetch contacts and return {contact_id: phone_or_None}."""
    result = {}
    batch_size = 250
    for i in range(0, len(contact_ids), batch_size):
        chunk = contact_ids[i:i+batch_size]
        qs = "&".join(f"filter[id][]={cid}" for cid in chunk)
        try:
            data = api_get(f"contacts?limit={batch_size}&{qs}")
        except Exception:
            continue
        for contact in data.get("_embedded", {}).get("contacts", []):
            phone = None
            for cf in (contact.get("custom_fields_values") or []):
                if cf.get("field_code") == "PHONE" or cf.get("field_name") == "Телефон":
                    vals = cf.get("values") or []
                    if vals:
                        phone = vals[0].get("value")
                        break
            result[contact["id"]] = phone
    return result


# ── Build ─────────────────────────────────────────────────────────────────────

def build_report():
    print("Fetching pipelines…")
    statuses = fetch_pipelines()

    print("Resolving payment date field…")
    payment_date_fid = resolve_payment_date_field_id()

    print("Fetching pipeline leads…")
    raw_leads = fetch_pipeline_leads(statuses, payment_date_fid)

    # Gather contact IDs for phone lookup
    contact_ids = []
    for lead in raw_leads:
        for emb_contact in (lead.get("_embedded", {}).get("contacts") or []):
            cid = emb_contact.get("id")
            if cid:
                contact_ids.append(cid)
    contact_ids = list(set(contact_ids))

    print(f"Fetching {len(contact_ids)} contacts for country detection…")
    contact_phones = fetch_contacts_phones(contact_ids)

    # Build lead → first contact phone map
    lead_phone = {}
    for lead in raw_leads:
        lid = lead.get("id")
        for emb_contact in (lead.get("_embedded", {}).get("contacts") or []):
            cid = emb_contact.get("id")
            phone = contact_phones.get(cid)
            if phone:
                lead_phone[lid] = phone
                break

    print("Fetching overdue tasks…")
    lead_ids_set = {l["id"] for l in raw_leads if l.get("id")}
    tasks_od = fetch_overdue_tasks_per_lead(lead_ids_set)

    print("Processing leads…")
    leads_data = []
    test_skipped = 0
    for lead in raw_leads:
        lid = lead.get("id")
        status_id = lead.get("status_id")

        # Skip ТЕСТ leads
        reason_val = None
        capital_val = None
        ready_val = None
        payment_ts = None

        for cf in (lead.get("custom_fields_values") or []):
            fid = cf.get("field_id")
            vals = cf.get("values") or []
            if not vals:
                continue
            v = vals[0].get("value")
            if fid == REASON_FIELD_ID:
                reason_val = v
            elif fid == CAPITAL_FIELD_ID:
                capital_val = v
            elif fid == READY_FIELD_ID:
                ready_val = v
            elif payment_date_fid and fid == payment_date_fid and v:
                try:
                    payment_ts = int(v)
                except (TypeError, ValueError):
                    pass

        if reason_val == TEST_REASON:
            test_skipped += 1
            continue

        # Country from phone
        phone = lead_phone.get(lid, "")
        country = phone_to_country(phone) if phone else "Не определено"

        closed_ts = lead.get("closed_at") or None

        leads_data.append({
            "id":      lid,
            "sid":     status_id,
            "mgr":     lead.get("responsible_user_id"),
            "c":       lead.get("created_at"),   # created_at
            "x":       closed_ts,                # closed_at (API)
            "p":       payment_ts,               # Дата оплаты (custom field)
            "price":   lead.get("price") or 0,
            "capital": capital_val,
            "ready":   ready_val,
            "reason":  reason_val,
            "country": country,
            "tod":     tasks_od.get(lid, 0),     # overdue task count
        })

    print(f"  {len(leads_data)} leads processed, {test_skipped} ТЕСТ skipped")

    # Status map for JS: {str(sid): {name, group}}
    status_map = {
        str(sid): {"name": info["name"], "group": info["group"]}
        for sid, info in statuses.items()
    }

    # Managers map for JS
    mgr_map = {str(k): v for k, v in MANAGERS.items()}

    now_str = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return {
        "leads":      leads_data,
        "statuses":   status_map,
        "managers":   mgr_map,
        "updated_at": now_str,
        "fetch_from": FETCH_FROM,
    }


# ── HTML Template ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ОП — Месячный дашборд</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:16px}
h1{font-size:20px;font-weight:700;color:#fff;margin-bottom:12px}
h2{font-size:14px;font-weight:600;color:#aaa;margin:18px 0 8px;text-transform:uppercase;letter-spacing:.5px}
.updated{font-size:11px;color:#555;margin-bottom:14px}

/* ── Filter bar ── */
.filter-bar{background:#1a1d27;border-radius:10px;padding:14px 18px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
.filter-bar label{font-size:13px;color:#ccc;cursor:pointer;display:flex;align-items:center;gap:6px}
.filter-bar input[type=radio]{accent-color:#74b9ff}
.filter-bar input[type=date]{background:#252836;border:1px solid #333;border-radius:6px;color:#e0e0e0;padding:5px 10px;font-size:13px}
.filter-bar select{background:#252836;border:1px solid #333;border-radius:6px;color:#e0e0e0;padding:5px 10px;font-size:13px}
.filter-bar button{background:#74b9ff;color:#0f1117;border:none;border-radius:6px;padding:6px 16px;font-size:13px;font-weight:600;cursor:pointer}
.filter-bar button:hover{background:#a0c8ff}
.filter-sep{width:1px;height:24px;background:#333}
.filter-label{font-size:12px;color:#666;font-weight:600;text-transform:uppercase;letter-spacing:.5px}

/* ── Stat cards ── */
.stat-row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.stat{background:#1a1d27;border-radius:10px;padding:14px 18px;min-width:130px;flex:1}
.stat-value{font-size:26px;font-weight:700;line-height:1.1}
.stat-label{font-size:11px;color:#777;margin-top:4px;text-transform:uppercase;letter-spacing:.4px}
.stat.blue .stat-value{color:#74b9ff}
.stat.orange .stat-value{color:#f5a623}
.stat.red .stat-value{color:#eb4d4b}
.stat.purple .stat-value{color:#a29bfe}
.stat.green .stat-value{color:#6ab04c}
.stat.teal .stat-value{color:#00cec9}
.stat.yellow .stat-value{color:#ffd32a}

/* ── Chart sections ── */
.section{background:#1a1d27;border-radius:10px;padding:16px;margin-bottom:14px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.row3{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:900px){.row2,.row3{grid-template-columns:1fr}}
.chart-wrap{position:relative;width:100%}

/* ── Cohort table ── */
.cohort-wrap{overflow-x:auto;margin-top:8px}
.cohort-table{border-collapse:collapse;font-size:12px;width:100%}
.cohort-table th,.cohort-table td{border:1px solid #2a2d3a;padding:5px 8px;text-align:center;white-space:nowrap}
.cohort-table th{background:#252836;color:#aaa;font-weight:600}
.cohort-table td:first-child{text-align:left;color:#ccc}
.cohort-table td.immature{color:#555;font-style:italic}
.heat-0{background:#1a1d27}.heat-1{background:#1e3a2a}.heat-2{background:#1e4a2e}
.heat-3{background:#1e5c30}.heat-4{background:#2a7340}.heat-5{background:#6ab04c;color:#0f1117}

/* ── Manager detail table ── */
.mgr-table-wrap{overflow-x:auto;margin-top:10px}
.mgr-table{border-collapse:collapse;width:100%;font-size:12px}
.mgr-table th,.mgr-table td{border:1px solid #2a2d3a;padding:6px 10px;text-align:center;white-space:nowrap}
.mgr-table th{background:#252836;color:#aaa;font-weight:600}
.mgr-table td:first-child{text-align:left;color:#ddd}
.mgr-table tr:hover td{background:#1f2235}

/* ── Heatmap table ── */
.heatmap-wrap{overflow-x:auto;margin-top:8px}
.heatmap-tbl{border-collapse:collapse;font-size:11px;width:100%}
.heatmap-tbl th,.heatmap-tbl td{border:1px solid #2a2d3a;padding:4px 8px;text-align:center;white-space:nowrap}
.heatmap-tbl th{background:#252836;color:#aaa;font-weight:600}
.heatmap-tbl td:first-child{text-align:left;color:#ccc}
</style>
</head>
<body>

<h1>ОП — Месячный дашборд</h1>
<div class="updated" id="updated_at"></div>

<!-- ── Filter bar ── -->
<div class="filter-bar">
  <span class="filter-label">Дата</span>
  <label><input type="radio" name="datetype" value="created" checked> По созданию</label>
  <label><input type="radio" name="datetype" value="closed"> По закрытию / оплате</label>
  <div class="filter-sep"></div>
  <span class="filter-label">Период</span>
  <select id="month_quick" onchange="onMonthQuick()"></select>
  <div class="filter-sep"></div>
  <span class="filter-label">Диапазон</span>
  <input type="date" id="date_from"> — <input type="date" id="date_to">
  <button onclick="applyFilters()">Применить</button>
</div>

<!-- ── Stat cards ── -->
<div class="stat-row">
  <div class="stat blue"><div class="stat-value" id="sv_total">—</div><div class="stat-label">Всего лидов</div></div>
  <div class="stat teal"><div class="stat-value" id="sv_active">—</div><div class="stat-label">В работе</div></div>
  <div class="stat orange"><div class="stat-value" id="sv_ndz">—</div><div class="stat-label">НДЗ</div></div>
  <div class="stat yellow"><div class="stat-value" id="sv_offer">—</div><div class="stat-label">Оффер озвучен</div></div>
  <div class="stat purple"><div class="stat-value" id="sv_delayed">—</div><div class="stat-label">Отложенный спрос</div></div>
  <div class="stat purple"><div class="stat-value" id="sv_invoiced">—</div><div class="stat-label">Выставлен счет</div></div>
  <div class="stat green"><div class="stat-value" id="sv_sales">—</div><div class="stat-label">Продажи</div></div>
  <div class="stat green"><div class="stat-value" id="sv_conv">—</div><div class="stat-label">Конверсия в продажу</div></div>
  <div class="stat green"><div class="stat-value" id="sv_revenue">—</div><div class="stat-label">Сумма сделок, ₽</div></div>
  <div class="stat green"><div class="stat-value" id="sv_avg">—</div><div class="stat-label">Средний чек, ₽</div></div>
</div>

<!-- ── Daily chart ── -->
<div class="section">
  <h2 id="daily_title">Лиды по дням</h2>
  <div class="chart-wrap"><canvas id="dailyChart" height="90"></canvas></div>
</div>

<!-- ── Manager + Overdue ── -->
<div class="row2">
  <div class="section">
    <h2>Лиды по менеджерам</h2>
    <div class="chart-wrap"><canvas id="mgrChart" height="200"></canvas></div>
  </div>
  <div class="section">
    <h2>Просроченные задачи по менеджерам</h2>
    <div class="chart-wrap"><canvas id="overdueChart" height="200"></canvas></div>
  </div>
</div>

<!-- ── Funnel + Cohort ── -->
<div class="row2">
  <div class="section">
    <h2>Кумулятивная воронка</h2>
    <div class="chart-wrap"><canvas id="funnelChart" height="200"></canvas></div>
  </div>
  <div class="section">
    <h2>Когортная таблица конверсий по неделям</h2>
    <div class="cohort-wrap" id="cohortTableWrap"></div>
  </div>
</div>

<!-- ── Conversion week chart ── -->
<div class="section">
  <h2>Конверсия «Взято в работу → Контакт установлен» по неделям</h2>
  <div class="chart-wrap"><canvas id="convWeekChart" height="90"></canvas></div>
</div>

<!-- ── Manager sales charts ── -->
<div class="row2">
  <div class="section">
    <h2>Выручка по менеджерам, ₽</h2>
    <div class="chart-wrap"><canvas id="revenueChart" height="200"></canvas></div>
  </div>
  <div class="section">
    <h2>Количество продаж по менеджерам</h2>
    <div class="chart-wrap"><canvas id="salesCntChart" height="200"></canvas></div>
  </div>
</div>
<div class="row2">
  <div class="section">
    <h2>Конверсия «Взято в работу → Продажи» по менеджерам</h2>
    <div class="chart-wrap"><canvas id="convMgrChart" height="200"></canvas></div>
  </div>
  <div class="section">
    <h2>Средний чек по менеджерам, ₽</h2>
    <div class="chart-wrap"><canvas id="avgCheckChart" height="200"></canvas></div>
  </div>
</div>

<!-- ── Capital + Readiness ── -->
<div class="row2">
  <div class="section">
    <h2>Капитал клиентов</h2>
    <div class="chart-wrap" style="max-height:260px"><canvas id="capitalChart"></canvas></div>
  </div>
  <div class="section">
    <h2>Готовность присоединиться</h2>
    <div class="chart-wrap" style="max-height:260px"><canvas id="readyChart"></canvas></div>
  </div>
</div>

<!-- ── Country ── -->
<div class="section">
  <h2>Лиды по странам (топ-10)</h2>
  <div class="chart-wrap"><canvas id="countryChart" height="100"></canvas></div>
</div>
<div class="section">
  <h2>Статусы по странам (топ-10)</h2>
  <div class="chart-wrap"><canvas id="countryStatusChart" height="120"></canvas></div>
</div>
<div class="section">
  <h2>Менеджеры × страны (количество лидов)</h2>
  <div class="heatmap-wrap" id="mgrCountryWrap"></div>
</div>

<!-- ── Close reasons ── -->
<div class="section">
  <h2>Причины закрытия сделок</h2>
  <div class="chart-wrap"><canvas id="reasonChart" height="120"></canvas></div>
</div>

<!-- ── Manager detail table ── -->
<div class="section">
  <h2>Детализация по менеджерам</h2>
  <div class="mgr-table-wrap" id="mgrTableWrap"></div>
</div>

<!-- ── Scripts ── -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<script>
Chart.register(ChartDataLabels);

const DATA = __JSON_DATA__;

// ── Constants ────────────────────────────────────────────────────────────────

const STATUSES  = DATA.statuses;   // {sid_str: {name, group}}
const MANAGERS  = DATA.managers;   // {uid_str: name}
const MGR_IDS   = Object.keys(MANAGERS).map(Number);

const VIZ_ORDER  = ["incoming","new_lead","om","in_work","contact","qualified","ndz","offer","delayed","sale","lost"];
const VIZ_LABELS = {incoming:"Входящие",new_lead:"Новый лид",om:"ОМ назначен",
  in_work:"Взято в работу",contact:"Контакт установлен",qualified:"Квалифицирован",
  ndz:"НДЗ",offer:"Оффер озвучен",delayed:"Отложен",sale:"Продажи+",lost:"Потеряно"};
const VIZ_COLORS = {incoming:"#74b9ff",new_lead:"#0984e3",om:"#6c5ce7",in_work:"#00cec9",
  contact:"#ffd32a",qualified:"#ff6b81",ndz:"#f5a623",offer:"#7ed6df",
  delayed:"#a29bfe",sale:"#6ab04c",lost:"#eb4d4b"};
const VIZ_GROUP  = {incoming:"incoming",new_lead:"new_lead",om:"om",in_work:"in_work",
  contact:"contact",qualified:"qualified",ndz:"ndz",offer:"offer",delayed:"delayed",
  invoiced:"sale",excursion:"sale",installment:"sale",sale:"sale",lost:"lost",active:"in_work"};

const FUNNEL_STAGES = [
  {grp:"new_lead", label:"Новый лид"},
  {grp:"in_work",  label:"Взято в работу"},
  {grp:"contact",  label:"Контакт установлен"},
  {grp:"qualified",label:"Квалифицирован"},
  {grp:"offer",    label:"Оффер озвучен"},
  {grp:"invoiced", label:"Выставлен счет"},
  {grp:"sale",     label:"Продажи"},
];
// Position in funnel (for cumulation)
const FUNNEL_POS = {incoming:1,new_lead:1,om:1,in_work:2,contact:3,qualified:4,
  ndz:2,offer:5,delayed:5,invoiced:6,excursion:6,sale:7,lost:2,active:2};
const STAGE_GRPS_FOR_COHORT = ["in_work","contact","qualified","offer","invoiced","sale"];
const STAGE_LABELS_COHORT   = ["Взято в работу","Контакт","Квалифицирован","Оффер","Выст. счет","Продажи"];

const CAPITAL_ORDER = ["До $10,000","$10,000-30,000","$30,000-50,000","$50,000-100,000","$100,000-500,000","$500,000+","Не указан"];
const INVALID_REASONS = new Set(["Дубль","тест","спам","некорректные данные","уже покупал Инфинити","уже покупал ментор"]);

// ── Chart registry ───────────────────────────────────────────────────────────

const _charts = {};
function upsertChart(id, config) {
  if (_charts[id]) { _charts[id].destroy(); }
  const ctx = document.getElementById(id);
  if (!ctx) return;
  _charts[id] = new Chart(ctx, config);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function numFmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1).replace('.',',')+' млн';
  return n.toLocaleString('ru-RU');
}
function pctFmt(n,d) { return d>0 ? Math.round(n/d*100)+'%' : '0%'; }
function tsToDate(ts) { return ts ? new Date(ts*1000) : null; }
function dateStr(d) {
  if (!d) return null;
  return d.toISOString().slice(0,10);
}
function addDays(d, n) { const r=new Date(d); r.setDate(r.getDate()+n); return r; }
function mondayOf(d) {
  const day = d.getDay();
  const r = new Date(d);
  r.setDate(d.getDate() - (day===0 ? 6 : day-1));
  r.setHours(0,0,0,0);
  return r;
}

// ── Filter ───────────────────────────────────────────────────────────────────

function getEffectiveTs(lead, mode) {
  if (mode === 'created') return lead.c;
  // 'closed' mode: prefer closed_at, fallback to payment_date
  return lead.x || lead.p || null;
}

function filterLeads(fromTs, toTs, mode) {
  return DATA.leads.filter(lead => {
    const ts = getEffectiveTs(lead, mode);
    if (!ts) return false;
    return ts >= fromTs && ts <= toTs;
  });
}

// ── Date controls ────────────────────────────────────────────────────────────

function populateMonthDropdown() {
  const sel = document.getElementById('month_quick');
  const now = new Date();
  // Build months from July 2026 to current
  const start = new Date(2026, 6, 1); // July 2026
  const months = [];
  let d = new Date(start);
  while (d <= now) {
    months.unshift(new Date(d));
    d.setMonth(d.getMonth()+1);
  }
  months.forEach((m, i) => {
    const opt = document.createElement('option');
    opt.value = m.toISOString().slice(0,7); // YYYY-MM
    const mNames = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    opt.textContent = mNames[m.getMonth()] + ' ' + m.getFullYear();
    if (i === 0) opt.selected = true;
    sel.appendChild(opt);
  });
}

function onMonthQuick() {
  const val = document.getElementById('month_quick').value; // YYYY-MM
  const [y, m] = val.split('-').map(Number);
  const from = new Date(y, m-1, 1);
  const to   = new Date(y, m, 0);   // last day of month
  document.getElementById('date_from').value = dateStr(from);
  document.getElementById('date_to').value   = dateStr(to);
  applyFilters();
}

function setDefaultDates() {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth();
  const from = new Date(y, m, 1);
  const to   = new Date(y, m+1, 0);
  document.getElementById('date_from').value = dateStr(from);
  document.getElementById('date_to').value   = dateStr(to);
}

// ── Main apply ───────────────────────────────────────────────────────────────

function applyFilters() {
  const mode    = document.querySelector('[name=datetype]:checked').value;
  const fromStr = document.getElementById('date_from').value;
  const toStr   = document.getElementById('date_to').value;
  if (!fromStr || !toStr) return;

  const fromTs = Math.floor(new Date(fromStr+'T00:00:00Z').getTime()/1000);
  const toTs   = Math.floor(new Date(toStr +'T23:59:59Z').getTime()/1000);

  document.getElementById('daily_title').textContent =
    mode === 'created' ? 'Лиды по дням (дата создания)' : 'Сделки по дням (дата закрытия / оплаты)';

  const leads = filterLeads(fromTs, toTs, mode);
  renderAll(leads, mode, fromStr, toStr);
}

// ── Render all ───────────────────────────────────────────────────────────────

function renderAll(leads, mode, fromStr, toStr) {
  updateStatCards(leads);
  renderDailyChart(leads, mode, fromStr, toStr);
  renderMgrChart(leads);
  renderOverdueChart(leads);
  renderFunnelChart(leads);
  renderCohortTable(leads);
  renderConvWeekChart(leads);
  renderRevenueChart(leads);
  renderSalesCntChart(leads);
  renderConvMgrChart(leads);
  renderAvgCheckChart(leads);
  renderCapitalChart(leads);
  renderReadyChart(leads);
  renderCountryChart(leads);
  renderCountryStatusChart(leads);
  renderMgrCountryHeatmap(leads);
  renderReasonChart(leads);
  renderMgrTable(leads);
}

// ── Stat cards ───────────────────────────────────────────────────────────────

function grpOf(lead) {
  const s = STATUSES[String(lead.sid)];
  return s ? s.group : 'active';
}
function vizGrpOf(lead) { return VIZ_GROUP[grpOf(lead)] || 'in_work'; }

function updateStatCards(leads) {
  const total   = leads.length;
  let ndz=0,offer=0,delayed=0,invoiced=0,sales=0,revenue=0;
  let inWork=0,contact=0,qualified=0;
  for (const l of leads) {
    const g = grpOf(l);
    if (g==='ndz')      ndz++;
    else if (g==='offer')    offer++;
    else if (g==='delayed')  delayed++;
    else if (g==='invoiced') invoiced++;
    else if (g==='sale')   { sales++; revenue += l.price||0; }
    else if (g==='in_work')  inWork++;
    else if (g==='contact')  contact++;
    else if (g==='qualified') qualified++;
  }
  const active = inWork + contact + qualified;
  const inWorkTotal = inWork + contact + qualified + ndz + offer + delayed + invoiced + sales;
  const conv = inWorkTotal>0 ? (sales/inWorkTotal*100).toFixed(1)+'%' : '—';
  const avg = sales>0 ? Math.round(revenue/sales) : 0;

  document.getElementById('sv_total').textContent    = numFmt(total);
  document.getElementById('sv_active').textContent   = numFmt(active);
  document.getElementById('sv_ndz').textContent      = numFmt(ndz);
  document.getElementById('sv_offer').textContent    = numFmt(offer);
  document.getElementById('sv_delayed').textContent  = numFmt(delayed);
  document.getElementById('sv_invoiced').textContent = numFmt(invoiced);
  document.getElementById('sv_sales').textContent    = numFmt(sales);
  document.getElementById('sv_conv').textContent     = conv;
  document.getElementById('sv_revenue').textContent  = numFmt(revenue);
  document.getElementById('sv_avg').textContent      = avg>0 ? numFmt(avg) : '—';
}

// ── Daily chart ──────────────────────────────────────────────────────────────

function renderDailyChart(leads, mode, fromStr, toStr) {
  const counts = {};
  // Pre-fill every day in range
  let d = new Date(fromStr+'T00:00:00Z');
  const end = new Date(toStr+'T00:00:00Z');
  while (d <= end) { counts[dateStr(d)] = 0; d = addDays(d,1); }

  for (const l of leads) {
    const ts = getEffectiveTs(l, mode);
    const ds = ts ? dateStr(new Date(ts*1000)) : null;
    if (ds && counts[ds] !== undefined) counts[ds]++;
  }

  const labels = Object.keys(counts).sort();
  const values = labels.map(k => counts[k]);

  upsertChart('dailyChart', {
    type: 'bar',
    data: { labels, datasets: [{
      data: values, backgroundColor: '#74b9ff', borderRadius: 3,
      label: mode==='created' ? 'Создано лидов' : 'Закрыто сделок',
    }]},
    options: {
      responsive: true, plugins: { legend: {display:false},
        datalabels: { color:'#aaa', font:{size:10}, anchor:'end', align:'end',
          formatter: v => v>0 ? v : '' }},
      scales: { x: { ticks:{color:'#777',maxRotation:45}, grid:{color:'#1f2235'} },
                y: { ticks:{color:'#777'}, grid:{color:'#1f2235'} } }
    }
  });
}

// ── Manager chart ────────────────────────────────────────────────────────────

function renderMgrChart(leads) {
  const mgrData = {};
  for (const uid of MGR_IDS) mgrData[uid] = {};
  for (const l of leads) {
    const uid = l.mgr;
    if (!mgrData[uid]) continue;
    const vg = vizGrpOf(l);
    mgrData[uid][vg] = (mgrData[uid][vg]||0)+1;
  }
  const mgrNames = MGR_IDS.map(id => MANAGERS[id]).filter((_,i)=>{
    return MGR_IDS.some(uid => uid===MGR_IDS[i] && Object.values(mgrData[MGR_IDS[i]]).some(v=>v>0));
  });
  const usedIds  = MGR_IDS.filter(uid => Object.values(mgrData[uid]).some(v=>v>0));
  const labels   = usedIds.map(uid => MANAGERS[uid]);

  const datasets = VIZ_ORDER.map(grp => ({
    label: VIZ_LABELS[grp],
    data:  usedIds.map(uid => mgrData[uid][grp]||0),
    backgroundColor: VIZ_COLORS[grp],
    stack: 'mgr',
  })).filter(ds => ds.data.some(v=>v>0));

  upsertChart('mgrChart', {
    type: 'bar',
    data: { labels, datasets },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend:{position:'bottom',labels:{color:'#aaa',boxWidth:12,font:{size:10}}},
        datalabels:{display:false} },
      scales: { x:{stacked:true,ticks:{color:'#777'},grid:{color:'#1f2235'}},
                y:{stacked:true,ticks:{color:'#ccc',font:{size:11}},grid:{display:false}} }
    }
  });
}

// ── Overdue tasks ────────────────────────────────────────────────────────────

function renderOverdueChart(leads) {
  const counts = {};
  for (const uid of MGR_IDS) counts[uid] = 0;
  for (const l of leads) {
    if (l.tod && counts[l.mgr] !== undefined) counts[l.mgr] += l.tod;
  }
  const usedIds = MGR_IDS.filter(uid => counts[uid]>0);
  if (!usedIds.length) {
    upsertChart('overdueChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},
      options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}});
    return;
  }
  upsertChart('overdueChart', {
    type: 'bar',
    data: { labels: usedIds.map(u=>MANAGERS[u]),
            datasets: [{data: usedIds.map(u=>counts[u]), backgroundColor:'#eb4d4b', borderRadius:3}] },
    options: { indexAxis:'y', responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'end',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc'},grid:{display:false}}} }
  });
}

// ── Cumulative funnel ────────────────────────────────────────────────────────

function renderFunnelChart(leads) {
  const stagePos = {new_lead:1,in_work:2,contact:3,qualified:4,offer:5,invoiced:6,sale:7};
  const counts = Array(FUNNEL_STAGES.length).fill(0);
  for (const l of leads) {
    const g = grpOf(l);
    const pos = FUNNEL_POS[g] || 0;
    FUNNEL_STAGES.forEach((st, i) => {
      if (pos >= (stagePos[st.grp]||0)) counts[i]++;
    });
  }
  upsertChart('funnelChart', {
    type:'bar',
    data:{labels: FUNNEL_STAGES.map(s=>s.label),
          datasets:[{data:counts,backgroundColor:'#74b9ff',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'end',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc',font:{size:11}},grid:{display:false}}} }
  });
}

// ── Cohort table ─────────────────────────────────────────────────────────────

function renderCohortTable(leads) {
  const groups = {};
  const now = Date.now()/1000;
  const MATURE_DAYS = 14;

  for (const l of leads) {
    const ts = l.c;
    if (!ts) continue;
    const d = new Date(ts*1000);
    const mon = mondayOf(d);
    const key = dateStr(mon);
    if (!groups[key]) groups[key] = [];
    groups[key].push(l);
  }

  const weeks = Object.keys(groups).sort();
  if (!weeks.length) { document.getElementById('cohortTableWrap').innerHTML='<p style="color:#555;font-size:12px">Нет данных</p>'; return; }

  const stagePos = {new_lead:1,in_work:2,contact:3,qualified:4,offer:5,invoiced:6,sale:7};

  let html = '<table class="cohort-table"><thead><tr><th>Неделя</th><th>Лидов</th>';
  STAGE_LABELS_COHORT.forEach(s => { html += `<th>${s}</th>`; });
  html += '</tr></thead><tbody>';

  const totals = Array(STAGE_GRPS_FOR_COHORT.length).fill(0);
  let totalLeads = 0;

  for (const wk of weeks) {
    const wLeads = groups[wk];
    const n = wLeads.length;
    totalLeads += n;
    const d = new Date(wk+'T00:00:00Z');
    const endD = addDays(d,6);
    const fmtD = (x) => x.getUTCDate().toString().padStart(2,'0')+'.'+(x.getUTCMonth()+1).toString().padStart(2,'0');
    const wLabel = fmtD(d)+'–'+fmtD(endD);
    const immature = (now - d.getTime()/1000) < MATURE_DAYS*86400;

    html += `<tr><td>${wLabel}</td><td>${n}</td>`;
    STAGE_GRPS_FOR_COHORT.forEach((sg,i) => {
      const minPos = stagePos[sg]||0;
      const cnt = wLeads.filter(l => (FUNNEL_POS[grpOf(l)]||0) >= minPos).length;
      totals[i] += cnt;
      const pct = n>0 ? Math.round(cnt/n*100) : 0;
      const heatIdx = Math.min(5, Math.floor(pct/20));
      const cls = immature ? 'immature' : `heat-${heatIdx}`;
      html += `<td class="${cls}">${immature ? `${pct}%*` : `${pct}%`}</td>`;
    });
    html += '</tr>';
  }

  // Totals row
  html += `<tr style="font-weight:600;background:#252836"><td>Итого</td><td>${totalLeads}</td>`;
  STAGE_GRPS_FOR_COHORT.forEach((sg,i) => {
    const pct = totalLeads>0 ? Math.round(totals[i]/totalLeads*100) : 0;
    html += `<td>${pct}%</td>`;
  });
  html += '</tr></tbody></table>';
  html += '<div style="font-size:11px;color:#555;margin-top:6px">* — неделя ещё не достигла 14 дней зрелости</div>';

  document.getElementById('cohortTableWrap').innerHTML = html;
}

// ── Conversion week chart (Взято → Контакт) ─────────────────────────────────

function renderConvWeekChart(leads) {
  const weekVzv = {}, weekKon = {};
  for (const l of leads) {
    if (!l.c) continue;
    const g = grpOf(l);
    const pos = FUNNEL_POS[g]||0;
    if (pos < 2) continue;  // didn't reach взято в работу
    const mon = mondayOf(new Date(l.c*1000));
    const k = dateStr(mon);
    weekVzv[k] = (weekVzv[k]||0)+1;
    if (pos >= 3) weekKon[k] = (weekKon[k]||0)+1;
  }
  const weeks = Object.keys(weekVzv).sort();
  if (!weeks.length) { upsertChart('convWeekChart',{type:'bar',data:{labels:[],datasets:[]},options:{plugins:{datalabels:{display:false}}}}); return; }

  const labels = weeks.map(w => {
    const d = new Date(w+'T00:00:00Z');
    const e = addDays(d,6);
    const fmt = x => x.getUTCDate().toString().padStart(2,'0')+'.'+(x.getUTCMonth()+1).toString().padStart(2,'0');
    return fmt(d)+'–'+fmt(e);
  });
  const vzvVals = weeks.map(w => weekVzv[w]||0);
  const konPct  = weeks.map(w => weekVzv[w] ? Math.round((weekKon[w]||0)/weekVzv[w]*100) : 0);

  upsertChart('convWeekChart', {
    type:'bar',
    data:{labels, datasets:[
      {type:'bar',label:'Взято в работу',data:vzvVals,backgroundColor:'#00cec9',yAxisID:'y',order:2},
      {type:'line',label:'Конверсия %',data:konPct,borderColor:'#ffd32a',backgroundColor:'#ffd32a22',
       yAxisID:'y2',tension:.3,pointRadius:4,order:1},
    ]},
    options:{responsive:true,
      plugins:{legend:{position:'bottom',labels:{color:'#aaa',font:{size:11}}},
        datalabels:{display:false}},
      scales:{
        x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
        y:{ticks:{color:'#777'},grid:{color:'#1f2235'},title:{display:true,text:'Лидов',color:'#555'}},
        y2:{position:'right',ticks:{color:'#ffd32a',callback:v=>v+'%'},grid:{display:false},
            suggestedMax:100,title:{display:true,text:'Конверсия %',color:'#555'}}
      }}
  });
}

// ── Revenue / sales count / avg check by manager ─────────────────────────────

function _salesByMgr(leads) {
  const rev = {}, cnt = {};
  for (const uid of MGR_IDS) { rev[uid]=0; cnt[uid]=0; }
  for (const l of leads) {
    if (grpOf(l)==='sale' && rev[l.mgr]!==undefined) {
      rev[l.mgr] += l.price||0;
      cnt[l.mgr]++;
    }
  }
  return {rev, cnt};
}

function renderRevenueChart(leads) {
  const {rev, cnt} = _salesByMgr(leads);
  const usedIds = MGR_IDS.filter(u => rev[u]>0);
  if (!usedIds.length) { upsertChart('revenueChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('revenueChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>rev[u]),backgroundColor:'#6ab04c',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:10},anchor:'end',align:'end',
          formatter:v=>v>=1e6?(v/1e6).toFixed(1).replace('.',',')+' млн':v>0?v.toLocaleString('ru-RU'):''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc'},grid:{display:false}}} }});
}

function renderSalesCntChart(leads) {
  const {cnt} = _salesByMgr(leads);
  const usedIds = MGR_IDS.filter(u => cnt[u]>0);
  if (!usedIds.length) { upsertChart('salesCntChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('salesCntChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>cnt[u]),backgroundColor:'#00cec9',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'end',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc'},grid:{display:false}}} }});
}

function renderConvMgrChart(leads) {
  const inWork = {}, sales = {};
  for (const uid of MGR_IDS) { inWork[uid]=0; sales[uid]=0; }
  for (const l of leads) {
    const pos = FUNNEL_POS[grpOf(l)]||0;
    if (pos>=2 && inWork[l.mgr]!==undefined) inWork[l.mgr]++;
    if (grpOf(l)==='sale' && sales[l.mgr]!==undefined) sales[l.mgr]++;
  }
  const usedIds = MGR_IDS.filter(u => inWork[u]>0 && MANAGERS[u] !== 'Виолетта Осадчук');
  if (!usedIds.length) { upsertChart('convMgrChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('convMgrChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>inWork[u]>0?Math.min(10,Math.round(sales[u]/inWork[u]*100)):0),
            backgroundColor:'#a29bfe',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'end',formatter:v=>v>0?v+'%':''}},
      scales:{x:{max:10,ticks:{color:'#777',callback:v=>v+'%'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc'},grid:{display:false}}} }});
}

function renderAvgCheckChart(leads) {
  const {rev, cnt} = _salesByMgr(leads);
  const usedIds = MGR_IDS.filter(u => cnt[u]>0);
  if (!usedIds.length) { upsertChart('avgCheckChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('avgCheckChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>cnt[u]>0?Math.round(rev[u]/cnt[u]):0),
            backgroundColor:'#fdcb6e',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:10},anchor:'end',align:'end',
          formatter:v=>v>0?v.toLocaleString('ru-RU'):''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc'},grid:{display:false}}} }});
}

// ── Capital + Readiness ──────────────────────────────────────────────────────

function renderCapitalChart(leads) {
  const counts = {};
  for (const l of leads) {
    const k = l.capital || 'Не указан';
    counts[k] = (counts[k]||0)+1;
  }
  const labels = CAPITAL_ORDER.filter(k=>counts[k]);
  for (const k of Object.keys(counts)) { if (!labels.includes(k)) labels.push(k); }
  const PIE_COLORS = ['#74b9ff','#0984e3','#6c5ce7','#00cec9','#ffd32a','#6ab04c','#a0a0a0'];
  upsertChart('capitalChart',{type:'doughnut',
    data:{labels,datasets:[{data:labels.map(k=>counts[k]),
      backgroundColor:PIE_COLORS.slice(0,labels.length)}]},
    options:{responsive:true,plugins:{
      legend:{position:'right',labels:{color:'#aaa',boxWidth:12,font:{size:11}}},
      datalabels:{color:'#fff',font:{size:10},formatter:(v,ctx)=>{
        const tot=ctx.dataset.data.reduce((a,b)=>a+b,0);
        return tot>0&&v/tot>0.05?v:'';
      }}}}});
}

function renderReadyChart(leads) {
  const counts = {};
  for (const l of leads) {
    let k = l.ready || 'Не ответил на вопрос';
    if (k==='Супер_Я_готов') k='Готов сейчас';
    counts[k] = (counts[k]||0)+1;
  }
  const labels = Object.keys(counts).sort();
  const READY_COLORS = ['#6ab04c','#ffd32a','#f5a623','#eb4d4b','#a0a0a0'];
  upsertChart('readyChart',{type:'doughnut',
    data:{labels,datasets:[{data:labels.map(k=>counts[k]),
      backgroundColor:READY_COLORS.slice(0,labels.length)}]},
    options:{responsive:true,plugins:{
      legend:{position:'right',labels:{color:'#aaa',boxWidth:12,font:{size:11}}},
      datalabels:{color:'#fff',font:{size:10},formatter:(v,ctx)=>{
        const tot=ctx.dataset.data.reduce((a,b)=>a+b,0);
        return tot>0&&v/tot>0.05?v:'';
      }}}}});
}

// ── Country charts ───────────────────────────────────────────────────────────

function _topCountries(leads, n) {
  const counts = {};
  for (const l of leads) { const c=l.country||'Не определено'; counts[c]=(counts[c]||0)+1; }
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  const top = sorted.slice(0,n).map(([c])=>c);
  return top;
}

function renderCountryChart(leads) {
  const counts = {};
  for (const l of leads) { const c=l.country||'Не определено'; counts[c]=(counts[c]||0)+1; }
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  const top10 = sorted.slice(0,10);
  const restSum = sorted.slice(10).reduce((s,[,v])=>s+v,0);
  const labels = top10.map(([c])=>c); if(restSum>0) labels.push('Прочие');
  const data   = top10.map(([,v])=>v); if(restSum>0) data.push(restSum);
  upsertChart('countryChart',{type:'bar',
    data:{labels,datasets:[{data,backgroundColor:'#74b9ff',borderRadius:3}]},
    options:{responsive:true,plugins:{legend:{display:false},
      datalabels:{color:'#aaa',font:{size:10},anchor:'end',align:'end',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#777'},grid:{color:'#1f2235'}}} }});
}

function renderCountryStatusChart(leads) {
  const topC = _topCountries(leads, 10);
  const STATUS_CGROUPS = [
    {key:'active', label:'В работе',    color:'#74b9ff'},
    {key:'offer',  label:'Оффер/Отложен',color:'#ffd32a'},
    {key:'sale',   label:'Продажи',     color:'#6ab04c'},
    {key:'ndz',    label:'НДЗ',         color:'#f5a623'},
    {key:'lost',   label:'Потеряно',    color:'#eb4d4b'},
  ];
  const CGRP_MAP = {incoming:'active',new_lead:'active',om:'active',in_work:'active',
    contact:'active',qualified:'active',ndz:'ndz',offer:'offer',delayed:'offer',
    invoiced:'sale',excursion:'sale',sale:'sale',lost:'lost',active:'active'};

  const data = {}; // {country: {cgrp: count}}
  for (const c of topC) data[c] = {};
  for (const l of leads) {
    const c = l.country||'Не определено';
    if (!topC.includes(c)) continue;
    const cg = CGRP_MAP[grpOf(l)]||'active';
    data[c][cg] = (data[c][cg]||0)+1;
  }
  const totals = {};
  for (const c of topC) totals[c] = Object.values(data[c]).reduce((s,v)=>s+v,0);

  const datasets = STATUS_CGROUPS.map(sg => ({
    label: sg.label,
    data: topC.map(c => totals[c]>0 ? Math.round((data[c][sg.key]||0)/totals[c]*100) : 0),
    backgroundColor: sg.color, stack:'s',
  }));

  upsertChart('countryStatusChart',{type:'bar',
    data:{labels:topC, datasets},
    options:{responsive:true,
      plugins:{legend:{position:'bottom',labels:{color:'#aaa',font:{size:11},boxWidth:12}},
        datalabels:{display:false}},
      scales:{
        x:{stacked:true,ticks:{color:'#777'},grid:{color:'#1f2235'}},
        y:{stacked:true,max:100,ticks:{color:'#777',callback:v=>v+'%'},grid:{color:'#1f2235'}}
      }}});
}

function renderMgrCountryHeatmap(leads) {
  const topC = _topCountries(leads, 10);
  const usedIds = MGR_IDS.filter(uid => leads.some(l=>l.mgr===uid));
  const data = {}; // {uid: {country: count}}
  for (const uid of usedIds) data[uid] = {};
  for (const l of leads) {
    if (!data[l.mgr]) continue;
    const c = l.country||'Не определено';
    if (topC.includes(c)) data[l.mgr][c] = (data[l.mgr][c]||0)+1;
  }
  const maxVal = Math.max(1, ...usedIds.flatMap(uid => topC.map(c => data[uid][c]||0)));

  let html = '<table class="heatmap-tbl"><thead><tr><th>Менеджер</th>';
  topC.forEach(c => { html += `<th>${c}</th>`; });
  html += '</tr></thead><tbody>';
  for (const uid of usedIds) {
    html += `<tr><td>${MANAGERS[uid]}</td>`;
    topC.forEach(c => {
      const v = data[uid][c]||0;
      const heat = Math.min(5, Math.round(v/maxVal*5));
      html += `<td class="heat-${heat}">${v||''}</td>`;
    });
    html += '</tr>';
  }
  html += '</tbody></table>';
  document.getElementById('mgrCountryWrap').innerHTML = html;
}

// ── Close reasons ────────────────────────────────────────────────────────────

function renderReasonChart(leads) {
  const counts = {};
  for (const l of leads) {
    const g = grpOf(l);
    if (g!=='lost' && g!=='ndz') continue;
    const r = l.reason;
    if (!r || INVALID_REASONS.has(r)) continue;
    counts[r] = (counts[r]||0)+1;
  }
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,15);
  if (!sorted.length) { upsertChart('reasonChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('reasonChart',{type:'bar',
    data:{labels:sorted.map(([r])=>r),datasets:[{data:sorted.map(([,v])=>v),backgroundColor:'#eb4d4b',borderRadius:3}]},
    options:{indexAxis:'y',responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'end',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
              y:{ticks:{color:'#ccc',font:{size:11}},grid:{display:false}}} }});
}

// ── Manager detail table ─────────────────────────────────────────────────────

function renderMgrTable(leads) {
  const rows = {};
  for (const uid of MGR_IDS) rows[uid] = {name:MANAGERS[uid],total:0,in_work:0,contact:0,qualified:0,offer:0,ndz:0,sale:0,revenue:0};
  for (const l of leads) {
    const r = rows[l.mgr]; if(!r) continue;
    const g = grpOf(l);
    r.total++;
    if (g==='in_work') r.in_work++;
    else if (g==='contact') r.contact++;
    else if (g==='qualified') r.qualified++;
    else if (g==='offer'||g==='delayed') r.offer++;
    else if (g==='ndz') r.ndz++;
    else if (g==='sale') { r.sale++; r.revenue+=l.price||0; }
  }
  const usedRows = MGR_IDS.map(uid=>rows[uid]).filter(r=>r.total>0);
  if (!usedRows.length) { document.getElementById('mgrTableWrap').innerHTML='<p style="color:#555;font-size:12px">Нет данных</p>'; return; }

  let html='<table class="mgr-table"><thead><tr>';
  ['Менеджер','Всего','В работе','Контакт','Квалиф.','Оффер/Откл.','НДЗ','Продажи','Выручка, ₽'].forEach(h=>{html+=`<th>${h}</th>`;});
  html+='</tr></thead><tbody>';
  for (const r of usedRows) {
    html+=`<tr><td>${r.name}</td><td>${r.total}</td><td>${r.in_work}</td><td>${r.contact}</td>`;
    html+=`<td>${r.qualified}</td><td>${r.offer}</td><td>${r.ndz}</td><td>${r.sale}</td>`;
    html+=`<td>${r.revenue>0?r.revenue.toLocaleString('ru-RU'):''}</td></tr>`;
  }
  html+='</tbody></table>';
  document.getElementById('mgrTableWrap').innerHTML=html;
}

// ── Init ─────────────────────────────────────────────────────────────────────

document.getElementById('updated_at').textContent = 'Обновлено: ' + DATA.updated_at;
populateMonthDropdown();
setDefaultDates();

// Sync dropdown to current month
const nowM = new Date();
const curKey = nowM.toISOString().slice(0,7);
const sel = document.getElementById('month_quick');
for (const opt of sel.options) { if (opt.value===curKey) { opt.selected=true; break; } }

applyFilters();

// Auto-reapply when radio changes
document.querySelectorAll('[name=datetype]').forEach(r => r.addEventListener('change', applyFilters));
</script>
</body>
</html>
"""


def generate_html(report):
    json_data = json.dumps(report, ensure_ascii=False, separators=(',', ':'))
    return HTML.replace("__JSON_DATA__", json_data)


if __name__ == "__main__":
    report = build_report()
    html   = generate_html(report)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done. Leads: {len(report['leads'])}, updated: {report['updated_at']}")
