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
TARIFF_FIELD_ID    = 1315345
PAYMENT_DATE_NAME  = "Дата оплаты"   # will be resolved at runtime

# Stage transition date fields (for SLA and velocity)
DATE_IN_WORK_FID  = 1318177  # Дата Взят в работу
DATE_CONTACT_FID  = 1318179  # Дата Контакт установлен
DATE_QUAL_FID     = 1318181  # Дата Квалифицирован
DATE_OFFER_FID    = 1318183  # Дата Оффер озвучен
DATE_INV_FID      = 1318187  # Дата Выставлен счет

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


def fetch_active_leads():
    """Fetch all currently open leads in the pipeline using a direct status filter.
    This is fast: the API returns only active-status leads, no post-filtering needed.
    Returns compact list [{mgr, sid}] — used for the manager snapshot chart."""
    EXCLUDED_GROUPS = {"sale", "lost"}

    # Get statuses for our specific pipeline
    try:
        pipe_data = api_get(f"leads/pipelines/{PIPELINE_ID}")
        pipeline_statuses = pipe_data.get("_embedded", {}).get("statuses", [])
    except Exception as e:
        print(f"  Warning fetching pipeline statuses: {e}")
        return []

    active_sids = [
        s["id"] for s in pipeline_statuses
        if STATUS_GROUPS.get(s["name"], "active") not in EXCLUDED_GROUPS
    ]
    if not active_sids:
        print("  No active statuses found — skipping active leads fetch")
        return []

    status_params = "&".join(
        f"filter[statuses][{i}][pipeline_id]={PIPELINE_ID}&filter[statuses][{i}][status_id]={sid}"
        for i, sid in enumerate(active_sids)
    )
    print(f"  Fetching active leads by status filter ({len(active_sids)} active statuses)…")

    leads = []
    page = 1
    MAX_PAGES = 20
    while page <= MAX_PAGES:
        path = f"leads?limit=250&page={page}&{status_params}&order[updated_at]=desc"
        try:
            data = api_get(path)
        except Exception as e:
            print(f"  Warning fetching active leads page {page}: {e}")
            break
        batch = data.get("_embedded", {}).get("leads", [])
        if not batch:
            break
        for l in batch:
            leads.append({
                "id":  l.get("id"),
                "mgr": l.get("responsible_user_id"),
                "sid": l.get("status_id"),
                "upd": l.get("updated_at"),
            })
        if len(batch) < 250:
            break
        page += 1
    print(f"  Active leads fetched: {len(leads)}")
    return leads


def fetch_future_tasks_for_active(lead_ids_set):
    """Returns set of lead_ids that have at least one active future task (not completed, complete_till > now)."""
    if not lead_ids_set:
        return set()
    now_ts = int(datetime.datetime.utcnow().timestamp())
    leads_with_task = set()
    page = 1
    MAX_PAGES = 10
    print(f"  Fetching future tasks for {len(lead_ids_set)} active leads…")
    while page <= MAX_PAGES:
        path = (f"tasks?limit=250&page={page}"
                f"&filter[is_completed]=0&filter[complete_till][from]={now_ts}")
        try:
            data = api_get(path)
        except Exception as e:
            print(f"  Warning fetching future tasks page {page}: {e}")
            break
        batch = data.get("_embedded", {}).get("tasks", [])
        if not batch:
            break
        for t in batch:
            if t.get("entity_type") == "leads":
                eid = t.get("entity_id")
                if eid in lead_ids_set:
                    leads_with_task.add(eid)
        if len(batch) < 250:
            break
        page += 1
    print(f"  Leads with future tasks: {len(leads_with_task)}")
    return leads_with_task


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


# ── Plans ─────────────────────────────────────────────────────────────────────

def load_plans():
    """Load plans.json; returns empty dict if file not found or invalid."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plans.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop("_comment", None)
        print(f"  Loaded plans.json ({len(data)} period(s))")
        return data
    except FileNotFoundError:
        print("  plans.json not found — plan cards will be hidden")
        return {}
    except Exception as e:
        print(f"  Warning loading plans.json: {e}")
        return {}


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
        tariff_val = None
        payment_ts = None
        d_w = d_c = d_q = d_o = d_i = None  # stage transition timestamps

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
            elif fid == TARIFF_FIELD_ID:
                tariff_val = v
            elif payment_date_fid and fid == payment_date_fid and v:
                try:
                    payment_ts = int(v)
                except (TypeError, ValueError):
                    pass
            elif fid == DATE_IN_WORK_FID and v:
                try: d_w = int(v)
                except (TypeError, ValueError): pass
            elif fid == DATE_CONTACT_FID and v:
                try: d_c = int(v)
                except (TypeError, ValueError): pass
            elif fid == DATE_QUAL_FID and v:
                try: d_q = int(v)
                except (TypeError, ValueError): pass
            elif fid == DATE_OFFER_FID and v:
                try: d_o = int(v)
                except (TypeError, ValueError): pass
            elif fid == DATE_INV_FID and v:
                try: d_i = int(v)
                except (TypeError, ValueError): pass

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
            "tariff":  tariff_val,
            "country": country,
            "tod":     tasks_od.get(lid, 0),     # overdue task count
            "d_w":     d_w,   # Дата Взят в работу
            "d_c":     d_c,   # Дата Контакт установлен
            "d_q":     d_q,   # Дата Квалифицирован
            "d_o":     d_o,   # Дата Оффер озвучен
            "d_i":     d_i,   # Дата Выставлен счет
        })

    print(f"  {len(leads_data)} leads processed, {test_skipped} ТЕСТ skipped")

    # Status map for JS: {str(sid): {name, group}}
    status_map = {
        str(sid): {"name": info["name"], "group": info["group"]}
        for sid, info in statuses.items()
    }

    # Manager snapshot: all currently open deals (ignores date filter)
    print("Fetching active leads snapshot…")
    active_leads_raw = fetch_active_leads()
    active_lead_ids = {l["id"] for l in active_leads_raw if l.get("id")}

    print("Fetching future tasks for active leads…")
    leads_with_future_task = fetch_future_tasks_for_active(active_lead_ids)

    print("Fetching overdue tasks for active leads…")
    active_tasks_od = fetch_overdue_tasks_per_lead(active_lead_ids)

    active_leads_data = [
        {
            "mgr": l["mgr"],
            "sid": l["sid"],
            "upd": l.get("upd"),
            "ht":  (l["id"] in leads_with_future_task) if l.get("id") else False,
            "tod": active_tasks_od.get(l["id"], 0),
        }
        for l in active_leads_raw
        if l.get("mgr") in MANAGERS
    ]

    # Managers map for JS
    mgr_map = {str(k): v for k, v in MANAGERS.items()}

    now_str = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    plans   = load_plans()

    return {
        "leads":        leads_data,
        "active_leads": active_leads_data,
        "statuses":     status_map,
        "managers":     mgr_map,
        "updated_at":   now_str,
        "fetch_from":   FETCH_FROM,
        "plans":        plans,
    }


# ── HTML Template ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ОП — Месячный дашборд</title>
<style>
:root{
  --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;
  --text:#e8eaf0;--muted:#8b8fa8;--accent:#4f8ef7;
  --green:#6ab04c;--orange:#f5a623;--red:#eb4d4b;--blue:#7ed6df;--purple:#a29bfe;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);font-size:14px;padding:24px}
h1{font-size:20px;font-weight:600;margin-bottom:4px}
h2{font-size:15px;font-weight:600;margin:32px 0 14px;color:var(--text)}
.meta{color:var(--muted);font-size:12px;margin-bottom:8px}

/* ── Filter bar ── */
.filter-bar{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:24px;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
.filter-bar label{font-size:13px;color:var(--text);cursor:pointer;display:flex;align-items:center;gap:6px}
.filter-bar input[type=radio]{accent-color:var(--accent)}
.filter-bar input[type=date]{background:#252836;border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 10px;font-size:13px}
.filter-bar select{background:#252836;border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 10px;font-size:13px}
.filter-bar button{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:6px 16px;font-size:13px;font-weight:600;cursor:pointer}
.filter-bar button:hover{opacity:.85}
.filter-sep{width:1px;height:24px;background:var(--border)}
.filter-label{font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}

/* ── Stat cards ── */
.stat-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:8px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.stat-value{font-size:28px;font-weight:700;line-height:1}
.stat-label{color:var(--muted);font-size:11px;margin-top:6px;text-transform:uppercase;letter-spacing:.04em}
.stat.accent .stat-value{color:var(--accent)}
.stat.orange .stat-value{color:var(--orange)}
.stat.red    .stat-value{color:var(--red)}
.stat.purple .stat-value{color:var(--purple)}
.stat.green  .stat-value{color:var(--green)}
.stat.blue   .stat-value{color:var(--blue)}
.stat.teal   .stat-value{color:#00cec9}
.stat.yellow .stat-value{color:#ffd32a}

/* ── Chart sections ── */
.section{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.row3{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:700px){.row2,.row3{grid-template-columns:1fr}}
.chart-wrap{position:relative;width:100%}

/* ── Cohort table ── */
.cohort-wrap{overflow-x:auto;margin-top:8px}
.cohort-table{border-collapse:collapse;font-size:12px;width:100%}
.cohort-table th,.cohort-table td{border:1px solid var(--border);padding:5px 8px;text-align:center;white-space:nowrap}
.cohort-table th{background:#22253a;color:var(--muted);font-weight:600}
.cohort-table td:first-child{text-align:left;color:var(--text)}
.cohort-table td.immature{color:#555;font-style:italic}
.heat-0{background:var(--surface)}.heat-1{background:#1e3a2a}.heat-2{background:#1e4a2e}
.heat-3{background:#1e5c30}.heat-4{background:#2a7340}.heat-5{background:#6ab04c;color:#0f1117}

/* ── Manager detail table ── */
.mgr-table-wrap{overflow-x:auto;margin-top:10px}
.mgr-table{border-collapse:collapse;width:100%;font-size:12px}
.mgr-table th,.mgr-table td{border:1px solid var(--border);padding:6px 10px;text-align:center;white-space:nowrap}
.mgr-table th{background:#22253a;color:var(--muted);font-weight:600}
.mgr-table td:first-child{text-align:left;color:var(--text)}
.mgr-table tr:hover td{background:#1e2133}

/* ── Heatmap table ── */
.heatmap-wrap{overflow-x:auto;margin-top:8px}
.heatmap-tbl{border-collapse:collapse;font-size:11px;width:100%}
.heatmap-tbl th,.heatmap-tbl td{border:1px solid var(--border);padding:4px 8px;text-align:center;white-space:nowrap}
.heatmap-tbl th{background:#22253a;color:var(--muted);font-weight:600}
.heatmap-tbl td:first-child{text-align:left;color:var(--text)}

.stat-plan-hidden{display:none!important}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
</style>
</head>
<body>

<h1>ОП — Месячный дашборд</h1>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
  <p class="meta" id="updated_at" style="margin:0"></p>
  <button id="refreshBtn" onclick="triggerRefresh()" style="background:var(--accent);color:#fff;border:none;border-radius:6px;padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px">
    <span id="refreshIcon">↻</span> <span id="refreshText">Обновить данные</span>
  </button>
</div>
<script>
function triggerRefresh() {
  const btn=document.getElementById('refreshBtn'),icon=document.getElementById('refreshIcon'),text=document.getElementById('refreshText');
  btn.disabled=true; btn.style.opacity='0.6';
  icon.style.animation='spin 1s linear infinite'; text.textContent='Запускаю обновление…';
  fetch('https://api.github.com/repos/Admin-web3a/op-dashboard/actions/workflows/daily.yml/dispatches',{
    method:'POST',
    headers:{'Authorization':'Bearer '+'github_pat_11B5MIWKI0FeFZwGIvGnUW_'+'k4r2oBZYBtLbjS5zKQ8tihNdCXgble7pSUn7ToJbVrg7O3G2T7V1NzRS5FV','Content-Type':'application/json'},
    body:JSON.stringify({ref:'main'})
  }).then(r=>{
    if(r.status===204){icon.style.animation='';icon.textContent='✓';text.textContent='Запущено! Обновите страницу через 3 мин.';btn.style.background='#6ab04c';btn.style.opacity='1';}
    else throw new Error('status '+r.status);
  }).catch(e=>{icon.style.animation='';icon.textContent='✕';text.textContent='Ошибка: '+e.message;btn.style.background='#eb4d4b';btn.style.opacity='1';btn.disabled=false;});
}
</script>

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

<!-- ── Stat cards: Group A — snapshot ── -->
<div style="display:flex;align-items:baseline;gap:10px;margin:4px 0 8px">
  <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)">Сейчас в воронке</span>
  <span style="font-size:11px;color:#555">Снимок всех открытых сделок · не зависит от фильтра дат</span>
</div>
<div class="stat-row" style="margin-bottom:20px">
  <div class="stat accent"><div class="stat-value" id="sv_a_work">—</div><div class="stat-label">В работе</div></div>
  <div class="stat orange"><div class="stat-value" id="sv_a_ndz">—</div><div class="stat-label">НДЗ</div></div>
  <div class="stat blue"><div class="stat-value" id="sv_a_offer">—</div><div class="stat-label">Оффер озвучен</div></div>
  <div class="stat blue"><div class="stat-value" id="sv_a_delayed">—</div><div class="stat-label">Отложенный спрос</div></div>
  <div class="stat purple"><div class="stat-value" id="sv_a_inv">—</div><div class="stat-label">Выставлен счёт</div></div>
</div>

<!-- ── Stat cards: Group B — period ── -->
<div style="display:flex;align-items:baseline;gap:10px;margin:0 0 8px">
  <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)">За период</span>
  <span id="period-label" style="font-size:12px;font-weight:600;color:var(--text)"></span>
  <span style="font-size:11px;color:#555">Новые лиды по дате создания · Продажи по дате закрытия / оплаты</span>
</div>
<div class="stat-row" style="margin-bottom:8px">
  <div class="stat"><div class="stat-value" id="sv_new">—</div><div class="stat-label">Новых лидов</div></div>
  <div class="stat green"><div class="stat-value" id="sv_sales">—</div><div class="stat-label">Продажи</div></div>
  <div class="stat"><div class="stat-value" id="sv_conv">—</div><div class="stat-label">Конверсия в продажу</div></div>
  <div class="stat green" style="min-width:160px"><div class="stat-value" id="sv_revenue" style="font-size:20px">—</div><div class="stat-label">Выручка, ₽</div></div>
  <div class="stat" style="min-width:160px"><div class="stat-value" id="sv_avg" style="font-size:20px">—</div><div class="stat-label">Средний чек, ₽</div></div>
  <div class="stat stat-plan-hidden" id="sv_plan_card"><div class="stat-value" id="sv_plan">—</div><div class="stat-label">% плана</div></div>
  <div class="stat stat-plan-hidden" id="sv_forecast_card"><div class="stat-value" id="sv_forecast" style="font-size:20px">—</div><div class="stat-label">Прогноз выручки, ₽</div></div>
</div>

<!-- ── Daily chart ── -->
<div class="section">
  <h2 id="daily_title">Лиды по дням</h2>
  <div class="chart-wrap"><canvas id="dailyChart" height="90"></canvas></div>
</div>

<!-- ── Manager ── -->
<div class="section">
  <h2>Лиды по менеджерам</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Все открытые сделки воронки, закреплённые за менеджером (без учёта фильтра дат). Продажи и закрытые сделки не включаются.</p>
  <div class="chart-wrap"><canvas id="mgrChart" height="130"></canvas></div>
</div>

<!-- ── Aging: stuck deals ── -->
<div class="section">
  <h2>Зависшие сделки</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Открытые сделки без движения по <code>updated_at</code>. Пороги — из <code>plans.json</code> (по умолчанию: жёлтый &gt;7 дн., красный &gt;14 дн.).</p>
  <div id="agingTableWrap"></div>
</div>

<!-- ── No active task ── -->
<div class="section">
  <h2>Лиды без активной задачи, %</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Доля открытых сделок без каких-либо незавершённых задач (ни будущих, ни просроченных) по каждому менеджеру.</p>
  <div class="chart-wrap"><canvas id="noTaskChart" height="90"></canvas></div>
</div>

<!-- ── SLA: creation → in work ── -->
<div class="section">
  <h2>SLA: создание лида → взято в работу (часы)</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Среднее время от создания лида до перевода в «Взято в работу» для лидов выбранного периода. Пунктир — норматив из <code>plans.json</code> (по умолчанию 4 ч).</p>
  <div class="chart-wrap"><canvas id="slaChart" height="90"></canvas></div>
</div>

<!-- ── SLA working hours ── -->
<div class="section">
  <h2>SLA: взятие в работу в рабочее время (часы)</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Рабочий SLA: время от начала рабочего дня менеджера (или от создания лида, если он пришёл в рабочее время) до взятия в работу. Ночные и нерабочие часы не считаются. 🟢 ≤ 1 ч · 🟡 ≤ 4 ч · 🔴 &gt; 4 ч.</p>
  <div class="chart-wrap"><canvas id="slaWorkChart" height="90"></canvas></div>
</div>

<!-- ── Velocity table ── -->
<div class="section">
  <h2>Скорость воронки по менеджерам (дней между этапами)</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Среднее время перехода между ключевыми этапами для лидов выбранного периода. Только лиды, прошедшие оба этапа.</p>
  <div id="velocityTableWrap"></div>
</div>

<!-- ── Overdue ── -->
<div class="section">
  <h2>Просроченные задачи по менеджерам</h2>
  <div class="chart-wrap"><canvas id="overdueChart" height="80"></canvas></div>
</div>

<!-- ── Funnel ── -->
<div class="section">
  <h2>Кумулятивная воронка</h2>
  <div class="chart-wrap"><canvas id="funnelChart" height="90"></canvas></div>
</div>

<!-- ── Cohort ── -->
<div class="section">
  <h2>Конверсия по неделям (когортный анализ)</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Лиды сгруппированы по дате создания (неделя пн–вс). * — незрелые когорты (&lt;14 дней), конверсия занижена.</p>
  <div id="cohortTableWrap"></div>
</div>

<!-- ── Weekly dynamics ── -->
<div class="section">
  <h2>Динамика неделя к неделе</h2>
  <p style="color:var(--muted);font-size:12px;margin:-10px 0 14px">Взято в работу по дате создания (столбцы) · Продажи по дате закрытия/оплаты (зелёная линия) · Конверсия % (правая ось).</p>
  <div class="chart-wrap"><canvas id="weeklyDynChart" height="100"></canvas></div>
</div>

<!-- ── Manager sales charts ── -->
<div class="row2">
  <div class="section">
    <h2>Выручка по менеджерам, ₽</h2>
    <div class="chart-wrap"><canvas id="revenueChart" height="160"></canvas></div>
  </div>
  <div class="section">
    <h2>Количество продаж по менеджерам</h2>
    <div class="chart-wrap"><canvas id="salesCntChart" height="160"></canvas></div>
  </div>
</div>
<div class="row2">
  <div class="section">
    <h2>Конверсия «Взято в работу → Продажи» по менеджерам</h2>
    <div class="chart-wrap"><canvas id="convMgrChart" height="160"></canvas></div>
  </div>
  <div class="section">
    <h2>Средний чек по менеджерам, ₽</h2>
    <div class="chart-wrap"><canvas id="avgCheckChart" height="160"></canvas></div>
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
<!-- ── Tariff & Close reasons ── -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start">
  <div class="section">
    <h2>Продажи по тарифам</h2>
    <div class="chart-wrap" style="height:320px"><canvas id="tariffChart"></canvas></div>
  </div>
  <div class="section">
    <h2>Причины закрытия сделок</h2>
    <div class="chart-wrap" style="height:320px"><canvas id="reasonChart"></canvas></div>
  </div>
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
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
function addDays(d, n) { const r=new Date(d); r.setUTCDate(r.getUTCDate()+n); return r; }
function mondayOf(d) {
  // Shift +3h so UTC date matches Moscow date, then find Monday in UTC
  const msk = new Date(d.getTime() + 3 * 3600 * 1000);
  const day = msk.getUTCDay();
  msk.setUTCDate(msk.getUTCDate() - (day === 0 ? 6 : day - 1));
  msk.setUTCHours(0, 0, 0, 0);
  return msk;
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
    opt.value = m.getFullYear() + '-' + String(m.getMonth() + 1).padStart(2, '0'); // YYYY-MM local
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

  const fromTs = Math.floor(new Date(fromStr+'T00:00:00').getTime()/1000);
  const toTs   = Math.floor(new Date(toStr +'T23:59:59').getTime()/1000);

  document.getElementById('daily_title').textContent =
    mode === 'created' ? 'Лиды по дням (дата создания)' : 'Сделки по дням (дата закрытия / оплаты)';

  const leads = filterLeads(fromTs, toTs, mode);
  renderAll(leads, mode, fromStr, toStr, fromTs, toTs);
}

// ── Render all ───────────────────────────────────────────────────────────────

function renderAll(leads, mode, fromStr, toStr, fromTs, toTs) {
  updateGroupBCards(fromTs, toTs, fromStr, toStr);
  renderDailyChart(leads, mode, fromStr, toStr);
  renderSlaChart(leads, fromTs, toTs);
  renderSlaWorkChart(leads, fromTs, toTs);
  renderVelocityTable(leads, fromTs, toTs);
  renderOverdueChart();
  renderFunnelChart(leads);
  renderCohortTable(leads);
  renderWeeklyDynamicsChart(leads);
  renderRevenueChart(leads, fromStr ? fromStr.slice(0,7) : null);
  renderSalesCntChart(leads);
  renderConvMgrChart(leads);
  renderAvgCheckChart(leads);
  renderCapitalChart(leads);
  renderReadyChart(leads);
  renderCountryChart(leads);
  renderCountryStatusChart(leads);
  renderReasonChart(leads);
  renderTariffChart(leads);
  renderMgrTable(leads);
}

// ── Stat cards ───────────────────────────────────────────────────────────────

function grpOf(lead) {
  const s = STATUSES[String(lead.sid)];
  return s ? s.group : 'active';
}
function vizGrpOf(lead) { return VIZ_GROUP[grpOf(lead)] || 'in_work'; }

// Group A: pipeline snapshot (active_leads, no date filter)
function initGroupACards() {
  let work=0, ndz=0, offer=0, delayed=0, invoiced=0;
  for (const l of DATA.active_leads) {
    const g = grpOf(l);
    if (g==='in_work'||g==='contact'||g==='qualified') work++;
    else if (g==='ndz')      ndz++;
    else if (g==='offer')    offer++;
    else if (g==='delayed')  delayed++;
    else if (g==='invoiced') invoiced++;
  }
  document.getElementById('sv_a_work').textContent    = numFmt(work);
  document.getElementById('sv_a_ndz').textContent     = numFmt(ndz);
  document.getElementById('sv_a_offer').textContent   = numFmt(offer);
  document.getElementById('sv_a_delayed').textContent = numFmt(delayed);
  document.getElementById('sv_a_inv').textContent     = numFmt(invoiced);
}

// Group B: period metrics (fixed attribution: new leads by created_at, sales by closed_at/payment)
function updateGroupBCards(fromTs, toTs, fromStr, toStr) {
  const leadsCreated = DATA.leads.filter(l => l.c && l.c >= fromTs && l.c <= toTs);
  const leadsClosed  = DATA.leads.filter(l => {
    const ts = l.x || l.p || null;
    return ts !== null && ts >= fromTs && ts <= toTs;
  });

  const newLeads = leadsCreated.length;
  let sales=0, revenue=0;
  for (const l of leadsClosed) {
    if (grpOf(l)==='sale') { sales++; revenue += l.price||0; }
  }
  // Conversion: sales (closing attribution) / взято-в-работу (creation attribution)
  const inWorkCnt = leadsCreated.filter(l => (FUNNEL_POS[grpOf(l)]||0) >= 2).length;
  const conv = inWorkCnt > 0 ? (sales/inWorkCnt*100).toFixed(1)+'%' : '—';
  const avg  = sales > 0 ? Math.round(revenue/sales) : 0;

  // Period label
  const mNames = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  const fd = new Date(fromStr+'T00:00:00'), td = new Date(toStr+'T00:00:00');
  const periodKey = fromStr ? fromStr.slice(0,7) : null;
  const labelEl = document.getElementById('period-label');
  if (labelEl) {
    if (fd.getUTCFullYear()===td.getUTCFullYear() && fd.getUTCMonth()===td.getUTCMonth())
      labelEl.textContent = mNames[fd.getUTCMonth()] + ' ' + fd.getUTCFullYear();
    else
      labelEl.textContent = fromStr + ' — ' + toStr;
  }

  document.getElementById('sv_new').textContent     = numFmt(newLeads);
  document.getElementById('sv_sales').textContent   = numFmt(sales);
  document.getElementById('sv_conv').textContent    = conv;
  document.getElementById('sv_revenue').textContent = numFmt(revenue);
  document.getElementById('sv_avg').textContent     = avg > 0 ? numFmt(avg) : '—';

  // Plan-based cards
  const plan = DATA.plans && periodKey ? DATA.plans[periodKey] : null;
  const planMgrs = plan && plan.managers ? Object.values(plan.managers) : [];
  const planRevTotal = planMgrs.reduce((s, m) => s + (m.revenue||0), 0);
  const planCard  = document.getElementById('sv_plan_card');
  const fcastCard = document.getElementById('sv_forecast_card');

  if (plan && planRevTotal > 0) {
    const pct   = Math.round(revenue / planRevTotal * 100);
    const pctEl = document.getElementById('sv_plan');
    pctEl.textContent = pct + '%';
    pctEl.style.color = pct >= 100 ? 'var(--green)' : pct >= 70 ? 'var(--orange)' : 'var(--red)';
    if (planCard) planCard.classList.remove('stat-plan-hidden');

    // Forecast only for the current calendar month
    const now    = new Date();
    const curKey = now.toISOString().slice(0,7);
    if (periodKey === curKey) {
      const startMs   = new Date(fromStr+'T00:00:00').getTime();
      const endMs     = new Date(toStr  +'T23:59:59').getTime();
      const totalDays   = Math.round((endMs - startMs) / 86400000);
      const elapsedDays = Math.max(1, Math.min(totalDays, Math.round((now.getTime() - startMs) / 86400000)));
      const forecast = Math.round(revenue * totalDays / elapsedDays);
      document.getElementById('sv_forecast').textContent = numFmt(forecast);
      if (fcastCard) fcastCard.classList.remove('stat-plan-hidden');
    } else {
      if (fcastCard) fcastCard.classList.add('stat-plan-hidden');
    }
  } else {
    if (planCard)  planCard.classList.add('stat-plan-hidden');
    if (fcastCard) fcastCard.classList.add('stat-plan-hidden');
  }
}

// ── Daily chart ──────────────────────────────────────────────────────────────

function renderDailyChart(leads, mode, fromStr, toStr) {
  const counts = {};
  // Pre-fill every day in range
  let d = new Date(fromStr+'T00:00:00');
  const end = new Date(toStr+'T00:00:00');
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

function renderMgrChart() {
  // Always uses the pipeline snapshot (all open deals), ignores date filter
  const leads = DATA.active_leads;
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

// ── Aging table ──────────────────────────────────────────────────────────────

function renderAgingTable() {
  const now = Date.now() / 1000;
  const curPeriod = new Date().toISOString().slice(0,7);
  const planData  = DATA.plans ? (DATA.plans[curPeriod] || {}) : {};
  const yellowDays = planData.aging_yellow_days || 7;
  const redDays    = planData.aging_red_days    || 14;

  const stats = {};
  for (const uid of MGR_IDS) stats[uid] = {total:0, sumAge:0, yellow:0, red:0};

  for (const l of DATA.active_leads) {
    const s = stats[l.mgr];
    if (!s || !l.upd) continue;
    const age = (now - l.upd) / 86400;
    s.total++;
    s.sumAge += age;
    if (age > yellowDays) s.yellow++;
    if (age > redDays)    s.red++;
  }

  const rows = MGR_IDS
    .filter(u => stats[u].total > 0)
    .map(u => ({u, ...stats[u], avg: (stats[u].sumAge / stats[u].total).toFixed(1)}))
    .sort((a,b) => b.red - a.red || b.yellow - a.yellow);

  const wrap = document.getElementById('agingTableWrap');
  if (!rows.length) { wrap.innerHTML='<p style="color:#555;font-size:12px">Нет данных</p>'; return; }

  const TH = 'background:#22253a;color:var(--muted);font-size:11px;font-weight:600;padding:9px 14px;text-align:center;';
  const TD = 'padding:8px 14px;text-align:center;border-top:1px solid var(--border);font-size:13px;';
  let html = `<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%">
    <thead><tr>
      <th style="${TH}text-align:left">Менеджер</th>
      <th style="${TH}">Всего</th>
      <th style="${TH}">Ср. дней</th>
      <th style="${TH}">&gt;${yellowDays} дн.</th>
      <th style="${TH}">&gt;${redDays} дн.</th>
    </tr></thead><tbody>`;

  for (const r of rows) {
    const ys = r.yellow > 0 ? 'color:#f5a623;font-weight:600' : 'color:#555';
    const rs = r.red    > 0 ? 'color:#eb4d4b;font-weight:600' : 'color:#555';
    html += `<tr>
      <td style="${TD}text-align:left;color:var(--text)">${MANAGERS[r.u]}</td>
      <td style="${TD}">${r.total}</td>
      <td style="${TD}">${r.avg}</td>
      <td style="${TD};${ys}">${r.yellow > 0 ? r.yellow : '—'}</td>
      <td style="${TD};${rs}">${r.red    > 0 ? r.red    : '—'}</td>
    </tr>`;
  }
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

// ── No active task chart ──────────────────────────────────────────────────────

function renderNoTaskChart() {
  const noTask = {}, total = {};
  for (const uid of MGR_IDS) { noTask[uid]=0; total[uid]=0; }
  for (const l of DATA.active_leads) {
    if (total[l.mgr] === undefined) continue;
    total[l.mgr]++;
    if (!l.ht && !l.tod) noTask[l.mgr]++;
  }
  const usedIds = MGR_IDS.filter(u => total[u] > 0);
  if (!usedIds.length) {
    upsertChart('noTaskChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},
      options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}});
    return;
  }
  const sorted = usedIds
    .map(u => ({u, pct: Math.round(noTask[u]/total[u]*100), nt: noTask[u], tot: total[u]}))
    .sort((a,b) => b.pct - a.pct);

  upsertChart('noTaskChart', {
    type: 'bar',
    data: {
      labels: sorted.map(x => MANAGERS[x.u]),
      datasets: [{
        data: sorted.map(x => x.pct),
        backgroundColor: sorted.map(x => x.pct > 50 ? '#eb4d4b' : x.pct > 25 ? '#f5a623' : '#6ab04c'),
        borderRadius: 4,
      }]
    },
    options: {responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:10},anchor:'end',align:'top',
          formatter:(v,ctx) => {
            const d = sorted[ctx.dataIndex];
            return v > 0 ? `${v}% (${d.nt}/${d.tot})` : '';
          }}},
      scales:{
        x:{ticks:{color:'#aaa',maxRotation:35,font:{size:10}},grid:{display:false}},
        y:{ticks:{color:'#777',callback:v=>v+'%'},grid:{color:'#1f2235'},max:100}
      }}
  });
}

// ── SLA: created → in-work ───────────────────────────────────────────────────

function renderSlaChart(leads, fromTs, toTs) {
  const curPeriod = new Date().toISOString().slice(0,7);
  const planData  = DATA.plans ? (DATA.plans[curPeriod] || {}) : {};
  const normH     = planData.sla_yellow_hours || 4;
  const redH      = planData.sla_red_hours    || 24;

  // Only leads CREATED in the selected period that have been taken in-work
  const stats = {};
  for (const uid of MGR_IDS) stats[uid] = {n: 0, sumH: 0, ok: 0, yellow: 0, red: 0};

  for (const l of DATA.leads) {
    if (!l.c || l.c < fromTs || l.c > toTs) continue; // created in period
    if (!l.d_w) continue; // not yet taken in-work
    const s = stats[l.mgr];
    if (!s) continue;
    const h = (l.d_w - l.c) / 3600;
    if (h < 0 || h > 720) continue; // skip anomalies (>30 days = data issue)
    s.n++;
    s.sumH += h;
    if (h <= normH)      s.ok++;
    else if (h <= redH)  s.yellow++;
    else                 s.red++;
  }

  const used = MGR_IDS.filter(u => stats[u].n > 0)
    .map(u => ({u, avg: stats[u].sumH / stats[u].n, ...stats[u]}))
    .sort((a, b) => a.avg - b.avg); // best first

  if (!used.length) {
    upsertChart('slaChart', {type:'bar', data:{labels:['Нет данных'], datasets:[{data:[0]}]},
      options:{plugins:{datalabels:{display:false}}, scales:{x:{ticks:{color:'#777'}}, y:{ticks:{color:'#777'}}}}});
    return;
  }

  upsertChart('slaChart', {
    type: 'bar',
    data: {
      labels: used.map(x => MANAGERS[x.u]),
      datasets: [{
        label: 'Среднее SLA, ч',
        data:  used.map(x => +x.avg.toFixed(1)),
        backgroundColor: used.map(x =>
          x.avg <= normH ? '#6ab04c' : x.avg <= redH ? '#f5a623' : '#eb4d4b'),
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: {display: false},
        annotation: {annotations: {norm: {
          type: 'line', yMin: normH, yMax: normH,
          borderColor: '#f5a623', borderWidth: 1.5, borderDash: [5,4],
          label: {content: 'Норма ' + normH + 'ч', display: true,
                  color: '#f5a623', font: {size: 10}, position: 'end'}
        }}},
        datalabels: {
          color: '#ccc', font: {size: 10}, anchor: 'end', align: 'top',
          formatter: (v, ctx) => {
            const d = used[ctx.dataIndex];
            const pct = Math.round(d.ok / d.n * 100);
            return v + 'ч · ' + d.n + ' лид. (' + pct + '% в норме)';
          }
        }
      },
      scales: {
        x: {ticks: {color: '#aaa', maxRotation: 35, font: {size: 10}}, grid: {display: false}},
        y: {ticks: {color: '#777', callback: v => v + 'ч'}, grid: {color: '#1f2235'}, beginAtZero: true}
      }
    }
  });
}

// ── SLA working hours chart ──────────────────────────────────────────────────

// Manager work schedules, Moscow time (UTC+4). start/end in hours.
const MGR_SCHEDULE = {
  "12377210": {start:  9, end: 18},  // Никита Саламатин
  "11181290": {start: 10, end: 19},  // Сергей
  "11176694": {start: 10, end: 19},  // Наталья
  "6461602":  {start: 14, end: 22},  // Зверева Елена
};
const MSK_OFFSET_SEC = 4 * 3600; // UTC+4

// Count working hours between two Unix timestamps for a given daily schedule.
// Weekend days are NOT excluded (managers work on a rolling duty basis).
function workHoursBetween(t1, t2, startH, endH) {
  if (!t1 || !t2 || t2 <= t1) return 0;
  const wsec = startH * 3600, esec = endH * 3600;

  // Find effective start: if t1 is before today's window → shift to window open;
  // if t1 is after today's window → shift to next day's window open.
  const msk1   = t1 + MSK_OFFSET_SEC;
  const base1  = Math.floor(msk1 / 86400) * 86400 - MSK_OFFSET_SEC;
  let cursor;
  if      (t1 < base1 + wsec) cursor = base1 + wsec;            // before window
  else if (t1 >= base1 + esec) cursor = base1 + 86400 + wsec;   // after window
  else                          cursor = t1;                      // inside window

  let hours = 0;
  let guard = 0;
  while (cursor < t2 && guard++ < 400) {
    const mskC  = cursor + MSK_OFFSET_SEC;
    const baseC = Math.floor(mskC / 86400) * 86400 - MSK_OFFSET_SEC;
    const winE  = baseC + esec;
    if (cursor >= winE) { cursor = baseC + 86400 + wsec; continue; }
    const from = cursor;
    const to   = Math.min(t2, winE);
    if (to > from) hours += (to - from) / 3600;
    cursor = baseC + 86400 + wsec;
  }
  return hours;
}

function renderSlaWorkChart(leads, fromTs, toTs) {
  const GREEN_H = 1, RED_H = 4;
  const stats = {};
  for (const uid of MGR_IDS) {
    if (MGR_SCHEDULE[String(uid)]) stats[uid] = {n:0, sumH:0, ok:0};
  }

  for (const l of leads) {
    if (!l.c || !l.d_w) continue;
    const sched = MGR_SCHEDULE[String(l.mgr)];
    if (!sched) continue;
    const s = stats[l.mgr];
    if (!s) continue;
    // Only count leads created in selected period
    if (l.c < fromTs || l.c > toTs) continue;
    const h = workHoursBetween(l.c, l.d_w, sched.start, sched.end);
    if (h < 0 || h > 200) continue;
    s.n++;
    s.sumH += h;
    if (h <= GREEN_H) s.ok++;
  }

  const used = Object.keys(stats)
    .map(Number)
    .filter(u => stats[u].n > 0)
    .map(u => ({u, avg: stats[u].sumH / stats[u].n, n: stats[u].n, ok: stats[u].ok}))
    .sort((a, b) => a.avg - b.avg);

  if (!used.length) {
    upsertChart('slaWorkChart', {type:'bar',
      data:{labels:['Нет данных'], datasets:[{data:[0]}]},
      options:{plugins:{datalabels:{display:false}},
               scales:{x:{ticks:{color:'#777'}}, y:{ticks:{color:'#777'}}}}});
    return;
  }

  upsertChart('slaWorkChart', {
    type: 'bar',
    data: {
      labels: used.map(x => MANAGERS[x.u]),
      datasets: [{
        label: 'Рабочий SLA, ч',
        data:  used.map(x => +x.avg.toFixed(2)),
        backgroundColor: used.map(x =>
          x.avg <= GREEN_H ? '#6ab04c' : x.avg <= RED_H ? '#f5a623' : '#eb4d4b'),
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: {display: false},
        datalabels: {
          color: '#ccc', font: {size: 10}, anchor: 'end', align: 'top',
          formatter: (v, ctx) => {
            const d = used[ctx.dataIndex];
            const pct = Math.round(d.ok / d.n * 100);
            return v + 'ч · ' + d.n + ' лид. (' + pct + '% ≤1ч)';
          }
        }
      },
      scales: {
        x: {ticks:{color:'#aaa', maxRotation:35, font:{size:10}}, grid:{display:false}},
        y: {ticks:{color:'#777', callback: v => v + 'ч'}, grid:{color:'#1f2235'}, beginAtZero:true}
      }
    }
  });
}

// ── Stage velocity table ──────────────────────────────────────────────────────

function renderVelocityTable(leads, fromTs, toTs) {
  // Stages: c→d_w, d_w→d_c, d_c→d_q, d_q→d_o
  const stages = [
    {key: 'c_iw',  label: 'Новый → В работу',         from: 'c',   to: 'd_w', unit: 'ч'},
    {key: 'iw_ct', label: 'В работе → Контакт',        from: 'd_w', to: 'd_c', unit: 'ч'},
    {key: 'ct_qu', label: 'Контакт → Квалифицирован',  from: 'd_c', to: 'd_q', unit: 'дн'},
    {key: 'qu_of', label: 'Квалиф. → Оффер озвучен',  from: 'd_q', to: 'd_o', unit: 'дн'},
  ];

  // Only leads CREATED in selected period
  const mgr_stats = {};
  for (const uid of MGR_IDS) {
    mgr_stats[uid] = {};
    for (const st of stages) mgr_stats[uid][st.key] = {n: 0, sum: 0};
  }

  for (const l of DATA.leads) {
    if (!l.c || l.c < fromTs || l.c > toTs) continue; // created in period
    const ms = mgr_stats[l.mgr];
    if (!ms) continue;
    for (const st of stages) {
      const t1 = l[st.from], t2 = l[st.to];
      if (!t1 || !t2 || t2 <= t1) continue;
      const diff = st.unit === 'ч' ? (t2 - t1) / 3600 : (t2 - t1) / 86400;
      ms[st.key].n++;
      ms[st.key].sum += diff;
    }
  }

  const rows = MGR_IDS
    .filter(u => stages.some(st => mgr_stats[u][st.key].n > 0))
    .map(u => ({u, stats: mgr_stats[u]}));

  const wrap = document.getElementById('velocityTableWrap');
  if (!rows.length) { wrap.innerHTML = '<p style="color:#555;font-size:12px">Нет данных за выбранный период</p>'; return; }

  const TH = 'background:#22253a;color:var(--muted);font-size:11px;font-weight:600;padding:9px 14px;text-align:center;white-space:nowrap';
  const TD = 'padding:8px 14px;text-align:center;border-top:1px solid var(--border);font-size:13px;';

  let html = `<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%"><thead><tr>
    <th style="${TH};text-align:left">Менеджер</th>`;
  for (const st of stages) html += `<th style="${TH}">${st.label}</th>`;
  html += '</tr></thead><tbody>';

  for (const row of rows) {
    html += `<tr><td style="${TD};text-align:left;color:var(--text)">${MANAGERS[row.u]}</td>`;
    for (const st of stages) {
      const s = row.stats[st.key];
      if (s.n === 0) {
        html += `<td style="${TD};color:#555">—</td>`;
      } else {
        const avg = (s.sum / s.n).toFixed(1);
        html += `<td style="${TD}">${avg} ${st.unit}</td>`;
      }
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

// ── Overdue tasks ────────────────────────────────────────────────────────────

function renderOverdueChart() {
  // Use active_leads snapshot (not period-filtered) so overdue count matches CRM
  const counts = {};
  for (const uid of MGR_IDS) counts[uid] = 0;
  for (const l of DATA.active_leads) {
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

// ── Cohort table (rich: stages as rows, weeks as cols + progress bars) ───────

const AF_STAGES = [
  {label:"Новый лид",          grps:new Set(["incoming","new_lead","om","in_work","contact","qualified","offer","delayed","invoiced","sale","ndz","lost"])},
  {label:"Взято в работу",     grps:new Set(["in_work","contact","qualified","offer","delayed","invoiced","sale","ndz","lost"])},
  {label:"Контакт установлен", grps:new Set(["contact","qualified","offer","invoiced","sale"])},
  {label:"Квалифицирован",     grps:new Set(["qualified","offer","invoiced","sale"])},
  {label:"Оффер озвучен",      grps:new Set(["offer","invoiced","sale"])},
  {label:"Выставлен счет",     grps:new Set(["invoiced","sale"])},
  {label:"Продажи",            grps:new Set(["sale"])},
];

function renderCohortTable(leads) {
  const MATURE_DAYS = 14;
  const nowTs = Date.now() / 1000;

  // Always group by creation date regardless of filter mode
  const weekMap = {};
  for (const l of leads) {
    if (!l.c) continue;
    const mon = mondayOf(new Date(l.c * 1000));
    const key = dateStr(mon);
    if (!weekMap[key]) weekMap[key] = [];
    weekMap[key].push(l);
  }

  const weekKeys = Object.keys(weekMap).sort();
  if (!weekKeys.length) {
    document.getElementById('cohortTableWrap').innerHTML = '<p style="color:#555;font-size:12px">Нет данных</p>';
    return;
  }

  const fmtD = x => x.getUTCDate().toString().padStart(2,'0') + '.' + (x.getUTCMonth()+1).toString().padStart(2,'0');
  const weeks = weekKeys.map(k => {
    const d = new Date(k + 'T00:00:00Z');
    return {
      key: k,
      label: fmtD(d) + '–' + fmtD(addDays(d, 6)),
      immature: (nowTs - d.getTime()/1000) < MATURE_DAYS * 86400,
      ls: weekMap[k],
    };
  });

  // counts[wi][si] = leads in week wi at stage si or deeper
  const counts = weeks.map(w =>
    AF_STAGES.map(st => w.ls.filter(l => st.grps.has(grpOf(l))).length)
  );
  // totals[si] = total across all leads
  const totals = AF_STAGES.map(st => leads.filter(l => st.grps.has(grpOf(l))).length);

  function convColor(pct) {
    if (pct >= 70) return '#6ab04c';
    if (pct >= 40) return '#f5a623';
    return '#eb4d4b';
  }
  function barCell(cnt, prev, immature, extraStyle) {
    const pct = prev > 0 ? Math.round(cnt / prev * 100) : 0;
    const col = convColor(pct);
    const op  = immature ? 'opacity:0.55;' : '';
    return '<td style="padding:6px 12px;' + op + (extraStyle||'') + '">'
      + '<div style="display:flex;align-items:center;gap:7px">'
      + '<div style="width:54px;height:7px;background:#2a2d3a;border-radius:3px;flex-shrink:0">'
      + '<div style="width:' + Math.min(100,pct) + '%;height:100%;background:' + col + ';border-radius:3px"></div>'
      + '</div>'
      + '<span style="font-size:12px;color:' + col + ';font-weight:600">' + pct + '%' + (immature?'*':'') + '</span>'
      + '</div></td>';
  }

  const TH = 'background:#22253a;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:10px 14px;';
  let html = '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;min-width:600px;font-size:13px">'
    + '<thead><tr><th style="' + TH + 'text-align:left;min-width:180px">Этап / Конверсия</th>';
  weeks.forEach(w => {
    html += '<th style="' + TH + 'text-align:center;' + (w.immature?'opacity:0.6':'') + '">'
      + w.label + (w.immature?' *':'') + '</th>';
  });
  html += '<th style="' + TH + 'text-align:center;background:#1a2e0a">ИТОГО</th></tr></thead><tbody>';

  AF_STAGES.forEach((st, si) => {
    html += '<tr style="border-top:2px solid var(--border)">'
      + '<td style="font-weight:600;color:var(--text);font-size:13px;padding:9px 14px">' + st.label + '</td>';
    weeks.forEach((w, wi) => {
      const cnt = counts[wi][si];
      html += '<td style="text-align:right;font-variant-numeric:tabular-nums;padding:9px 14px;'
        + (w.immature ? 'opacity:0.6' : '') + '">' + (cnt||'') + '</td>';
    });
    html += '<td style="text-align:right;font-variant-numeric:tabular-nums;font-weight:700;background:#1a1f0a;padding:9px 14px">'
      + totals[si] + '</td></tr>';

    if (si > 0) {
      html += '<tr><td style="font-size:11px;color:var(--muted);padding:4px 14px 4px 28px">↳ к предыдущему</td>';
      weeks.forEach((w, wi) => {
        html += barCell(counts[wi][si], counts[wi][si-1], w.immature, '');
      });
      html += barCell(totals[si], totals[si-1], false, 'background:#1a1f0a;');
      html += '</tr>';
    }
  });

  html += '</tbody></table>'
    + '<p style="font-size:11px;color:#555;margin-top:6px">* — неделя ещё не достигла 14 дней зрелости</p>'
    + '</div>';

  document.getElementById('cohortTableWrap').innerHTML = html;
}

// ── Weekly dynamics (Взято + Продажи + Конверсия) ───────────────────────────

function renderWeeklyDynamicsChart(leads) {
  // Bars: Взято в работу by creation week
  const weekVzv = {};
  for (const l of leads) {
    if (!l.c || (FUNNEL_POS[grpOf(l)]||0) < 2) continue;
    const k = dateStr(mondayOf(new Date(l.c * 1000)));
    weekVzv[k] = (weekVzv[k]||0) + 1;
  }
  // Line: sales by closing/payment week
  const weekSales = {};
  for (const l of leads) {
    const ts = l.x || l.p || null;
    if (!ts || grpOf(l) !== 'sale') continue;
    const k = dateStr(mondayOf(new Date(ts * 1000)));
    weekSales[k] = (weekSales[k]||0) + 1;
  }

  const allWeeks = new Set([...Object.keys(weekVzv), ...Object.keys(weekSales)]);
  const weeks = [...allWeeks].sort();
  if (!weeks.length) {
    upsertChart('weeklyDynChart',{type:'bar',data:{labels:[],datasets:[]},options:{plugins:{datalabels:{display:false}}}});
    return;
  }

  const fmtWeek = w => {
    const d = new Date(w+'T00:00:00Z'), e = addDays(d,6);
    const fmt = x => x.getUTCDate().toString().padStart(2,'0')+'.'+(x.getUTCMonth()+1).toString().padStart(2,'0');
    return fmt(d)+'–'+fmt(e);
  };
  const labels    = weeks.map(fmtWeek);
  const vzvVals   = weeks.map(w => weekVzv[w]||0);
  const salesVals = weeks.map(w => weekSales[w]||0);
  const convPct   = weeks.map(w => (weekVzv[w]||0) > 0 ? Math.round((weekSales[w]||0)/(weekVzv[w])*100) : 0);

  upsertChart('weeklyDynChart', {
    type:'bar',
    data:{labels, datasets:[
      {type:'bar',  label:'Взято в работу', data:vzvVals,  backgroundColor:'#00cec9', borderRadius:3, yAxisID:'y', order:3, datalabels:{display:false}},
      {type:'line', label:'Продажи',        data:salesVals, borderColor:'#6ab04c', backgroundColor:'#6ab04c22',
       tension:.3, pointRadius:4, fill:true, yAxisID:'y', order:2, datalabels:{display:false}},
      {type:'line', label:'Конверсия %',    data:convPct,   borderColor:'#ffd32a', backgroundColor:'transparent',
       tension:.3, pointRadius:4, yAxisID:'y2', order:1,
       datalabels:{display:true,color:'#ffd32a',font:{size:10,weight:'600'},anchor:'end',align:'top',
         formatter: v => v > 0 ? v+'%' : ''}},
    ]},
    options:{responsive:true,
      plugins:{
        legend:{position:'bottom',labels:{color:'#aaa',font:{size:11},boxWidth:12}},
        datalabels:{display: ctx => ctx.datasetIndex === 2}
      },
      scales:{
        x:{ticks:{color:'#777'},grid:{color:'#1f2235'}},
        y:{ticks:{color:'#777'},grid:{color:'#1f2235'},title:{display:true,text:'Лидов',color:'#555'}},
        y2:{position:'right',ticks:{color:'#ffd32a',callback:v=>v+'%'},grid:{display:false},
            suggestedMax:50,title:{display:true,text:'Конверсия',color:'#555'}}
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

function renderRevenueChart(leads, periodKey) {
  const {rev, cnt} = _salesByMgr(leads);
  const usedIds = MGR_IDS.filter(u => rev[u]>0).sort((a,b) => rev[b]-rev[a]);
  if (!usedIds.length) { upsertChart('revenueChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }

  const datasets = [{
    label: 'Выручка',
    data: usedIds.map(u=>rev[u]),
    backgroundColor: '#6ab04c',
    borderRadius: 4,
    order: 2,
  }];

  const plan = DATA.plans && periodKey ? DATA.plans[periodKey] : null;
  if (plan && plan.managers) {
    const planRevData = usedIds.map(u => {
      const v = plan.managers[String(u)];
      return v && v.revenue > 0 ? v.revenue : null;
    });
    if (planRevData.some(v => v !== null)) {
      datasets.push({
        type: 'line',
        label: 'План',
        data: planRevData,
        borderColor: '#ffd32a',
        borderDash: [5, 4],
        borderWidth: 2,
        pointRadius: 5,
        pointHoverRadius: 7,
        pointBackgroundColor: '#ffd32a',
        pointBorderColor: '#ffd32a',
        fill: false,
        order: 1,
        datalabels: { display: false },
      });
    }
  }

  const hasLegend = datasets.length > 1;
  upsertChart('revenueChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]), datasets},
    options:{responsive:true,
      plugins:{
        legend:{display:hasLegend,position:'bottom',labels:{color:'#aaa',font:{size:10},boxWidth:12}},
        datalabels:{
          display: ctx => ctx.datasetIndex === 0,
          color:'#ccc',font:{size:10},anchor:'end',align:'top',
          formatter:v=>v>=1e6?(v/1e6).toFixed(1).replace('.',',')+' млн':v>0?v.toLocaleString('ru-RU'):''
        }},
      scales:{x:{ticks:{color:'#aaa',maxRotation:35,font:{size:10}},grid:{display:false}},
              y:{ticks:{color:'#777'},grid:{color:'#1f2235'}} } }});
}

function renderSalesCntChart(leads) {
  const {cnt} = _salesByMgr(leads);
  const usedIds = MGR_IDS.filter(u => cnt[u]>0).sort((a,b) => cnt[b]-cnt[a]);
  if (!usedIds.length) { upsertChart('salesCntChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('salesCntChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>cnt[u]),backgroundColor:'#00cec9',borderRadius:4}]},
    options:{responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'top',formatter:v=>v>0?v:''}},
      scales:{x:{ticks:{color:'#aaa',maxRotation:35,font:{size:10}},grid:{display:false}},
              y:{ticks:{color:'#777'},grid:{color:'#1f2235'}} } }});
}

function renderConvMgrChart(leads) {
  const inWork = {}, sales = {};
  for (const uid of MGR_IDS) { inWork[uid]=0; sales[uid]=0; }
  for (const l of leads) {
    const pos = FUNNEL_POS[grpOf(l)]||0;
    if (pos>=2 && inWork[l.mgr]!==undefined) inWork[l.mgr]++;
    if (grpOf(l)==='sale' && sales[l.mgr]!==undefined) sales[l.mgr]++;
  }
  const convPct = uid => inWork[uid]>0 ? Math.round(sales[uid]/inWork[uid]*100) : 0;
  const usedIds = MGR_IDS.filter(u => inWork[u]>0 && MANAGERS[u] !== 'Виолетта Осадчук')
    .sort((a,b) => convPct(b)-convPct(a));
  if (!usedIds.length) { upsertChart('convMgrChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('convMgrChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>convPct(u)),
            backgroundColor:'#a29bfe',borderRadius:4}]},
    options:{responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:11},anchor:'end',align:'top',formatter:v=>v>0?v+'%':''}},
      scales:{x:{ticks:{color:'#aaa',maxRotation:35,font:{size:10}},grid:{display:false}},
              y:{ticks:{color:'#777',callback:v=>v+'%'},grid:{color:'#1f2235'}} } }});
}

function renderAvgCheckChart(leads) {
  const {rev, cnt} = _salesByMgr(leads);
  const avg = uid => cnt[uid]>0 ? Math.round(rev[uid]/cnt[uid]) : 0;
  const usedIds = MGR_IDS.filter(u => cnt[u]>0).sort((a,b) => avg(b)-avg(a));
  if (!usedIds.length) { upsertChart('avgCheckChart',{type:'bar',data:{labels:['Нет данных'],datasets:[{data:[0]}]},options:{plugins:{datalabels:{display:false}},scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}}); return; }
  upsertChart('avgCheckChart',{type:'bar',
    data:{labels:usedIds.map(u=>MANAGERS[u]),
          datasets:[{data:usedIds.map(u=>cnt[u]>0?Math.round(rev[u]/cnt[u]):0),
            backgroundColor:'#fdcb6e',borderRadius:4}]},
    options:{responsive:true,
      plugins:{legend:{display:false},
        datalabels:{color:'#ccc',font:{size:10},anchor:'end',align:'top',
          formatter:v=>v>0?v.toLocaleString('ru-RU'):''}},
      scales:{x:{ticks:{color:'#aaa',maxRotation:35,font:{size:10}},grid:{display:false}},
              y:{ticks:{color:'#777'},grid:{color:'#1f2235'}} } }});
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

// ── Tariff chart ─────────────────────────────────────────────────────────────

function renderTariffChart(leads) {
  const palette = [
    '#4f8ef7','#6ab04c','#f5a623','#a29bfe','#fd79a8','#00cec9',
    '#e17055','#fdcb6e','#74b9ff','#55efc4','#b2bec3','#636e72'
  ];
  const counts = {};
  for (const l of leads) {
    if (grpOf(l) !== 'sale') continue;
    const t = l.tariff || 'Не указан';
    counts[t] = (counts[t] || 0) + 1;
  }
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!sorted.length) {
    upsertChart('tariffChart', {type:'bar', data:{labels:['Нет данных'], datasets:[{data:[0]}]},
      options:{plugins:{datalabels:{display:false}}, scales:{x:{ticks:{color:'#777'}}, y:{ticks:{color:'#777'}}}}});
    return;
  }
  const labels = sorted.map(([t]) => t);
  const values = sorted.map(([, v]) => v);
  const total  = values.reduce((a, b) => a + b, 0);
  upsertChart('tariffChart', {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Продаж',
        data: values,
        backgroundColor: labels.map((_, i) => palette[i % palette.length]),
        borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      maintainAspectRatio: false,
      responsive: true,
      plugins: {
        legend: {display: false},
        tooltip: {callbacks: {label: function(c) {
          return ' ' + c.raw + ' сделок (' + Math.round(c.raw / total * 100) + '%)';
        }}},
        datalabels: {
          color: '#ccc', font: {size: 11}, anchor: 'end', align: 'end',
          formatter: (v) => v > 0 ? v : ''
        }
      },
      scales: {
        x: {beginAtZero: true, ticks: {color: '#e8eaf0', stepSize: 1}, grid: {color: '#1e2a3a'}},
        y: {ticks: {color: '#e8eaf0', font: {size: 12}}, grid: {color: '#1e2a3a'}}
      }
    }
  });
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

document.getElementById('updated_at').textContent = 'Источник: amoCRM simmihur · Обновлено: ' + DATA.updated_at;
populateMonthDropdown();
setDefaultDates();

// Sync dropdown to current month
const nowM = new Date();
const curKey = nowM.toISOString().slice(0,7);
const sel = document.getElementById('month_quick');
for (const opt of sel.options) { if (opt.value===curKey) { opt.selected=true; break; } }

// Group A: snapshot cards (drawn once, not affected by date filter)
initGroupACards();

applyFilters();

// Snapshot charts — drawn once, not affected by date filter
renderMgrChart();
renderAgingTable();
renderNoTaskChart();

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
