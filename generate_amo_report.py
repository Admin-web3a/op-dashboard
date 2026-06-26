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

SOURCE_FIELD_ID       = 1321741
SOURCE_PREREG_ID      = 953633       # Анкета перезаписи 06.2026 (known enum ID)
SOURCE_WEB_ORDER_NAME = "Заказ веб 06.26"      # будет разрешён по имени
SOURCE_WEB_PREPAY_NAME= "Предоплата веб 06.26" # будет разрешён по имени
UPDATED_FROM          = 1743465600   # 2026-04-01

REASON_FIELD_ID = 180637
TEST_REASON     = "ТЕСТ"            # Полное исключение — не попадает ни в один чарт
CONVERT_REASON  = "Оставил Заказ"  # Предзапись закрыта, т.к. клиент оформил заказ на вебинаре

# Причины закрытия, которые исключаем из чарта «Причины закрытия»
# (невалидные лиды — не реальные отказы)
INVALID_REASONS = {
    "Дубль", "тест", "спам", "некорректные данные",
    "уже покупал Инфинити", "уже покупал ментор",
}

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

def resolve_source_enum_ids():
    """Fetch SOURCE_FIELD_ID definition and return {enum_id: source_type}.
    Falls back gracefully if new sources aren't in CRM yet."""
    result = {SOURCE_PREREG_ID: "prereg"}
    try:
        data = api_get(f"leads/custom_fields/{SOURCE_FIELD_ID}")
        enums = data.get("enums", [])
        if not enums:
            # Some CRM setups return enums nested differently
            enums = data.get("values", [])
        name_map = {e.get("value", e.get("enum", "")): e["id"] for e in enums}
        for name, src_type in [
            (SOURCE_WEB_ORDER_NAME,  "web_order"),
            (SOURCE_WEB_PREPAY_NAME, "web_prepay"),
        ]:
            eid = name_map.get(name)
            if eid:
                result[eid] = src_type
                print(f"  Resolved '{name}' → enum_id {eid}")
            else:
                print(f"  '{name}' not found in CRM yet — will track when added")
    except Exception as e:
        print(f"  Warning: could not resolve source enums: {e}")
    return result


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

def fetch_filtered_leads(statuses, source_enum_ids):
    """Fetch leads matching any of source_enum_ids; tags each with lead['_source']."""
    filtered = []
    consecutive_empty = 0
    page = 1
    while True:
        path = (f"leads?limit=250&page={page}&with=contacts"
                f"&order[updated_at]=desc&filter[updated_at][from]={UPDATED_FROM}")
        data = api_get(path)
        batch = data.get("_embedded", {}).get("leads", [])
        if not batch:
            break
        matched = 0
        for lead in batch:
            is_test = any(
                cf.get("field_id") == REASON_FIELD_ID
                and any(v.get("value") == TEST_REASON for v in (cf.get("values") or []))
                for cf in (lead.get("custom_fields_values") or [])
            )
            if is_test:
                continue
            for cf in (lead.get("custom_fields_values") or []):
                if cf.get("field_id") == SOURCE_FIELD_ID:
                    for v in cf.get("values", []):
                        eid = v.get("enum_id")
                        if eid in source_enum_ids:
                            lead["_source"] = source_enum_ids[eid]
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
    "ndz":       0,   # excluded
    "lost":      0,   # excluded
}

# ── Attributed funnel ────────────────────────────────────────────────────────
# Business rules confirmed by user:
#   • Входящий чекин + ОМ назначен чекин + ОМ назначен → attributed to "Новый лид"
#     (pre-sales incoming pool, not yet worked by sales)
#   • НДЗ and Закрыто → attributed to "Взято в работу"
#     (sales always works a lead before it can be moved to НДЗ/Закрыто)
#   • Экскурсия → excluded from funnel entirely
# Each tuple: (display_name, frozenset_of_groups_that_count_for_this_stage)
ATTR_FUNNEL = [
    # "delayed" (Отложенный спрос) может выйти из воронки сразу после "Взято в работу",
    # поэтому атрибутируется только к "Новый лид" и "Взято в работу", но не ниже.
    ("Новый лид",
        frozenset({"incoming", "new_lead", "om", "in_work", "contact",
                   "qualified", "offer", "delayed", "invoiced", "sale",
                   "ndz", "lost"})),
    ("Взято в работу",
        frozenset({"in_work", "contact", "qualified",
                   "offer", "delayed", "invoiced", "sale",
                   "ndz", "lost"})),
    ("Контакт установлен",
        frozenset({"contact", "qualified", "offer", "invoiced", "sale"})),
    ("Квалифицирован",
        frozenset({"qualified", "offer", "invoiced", "sale"})),
    ("Оффер озвучен",
        frozenset({"offer", "invoiced", "sale"})),
    ("Выставлен счет",
        frozenset({"invoiced", "sale"})),
    ("Продажи",
        frozenset({"sale"})),
]

def compute_cumulative_funnel(leads, statuses):
    """Attributed funnel: each stage counts leads at that group or deeper.
    Excursion is excluded; НДЗ/Закрыто are attributed to Взято в работу."""
    lead_groups = [
        statuses.get(lead.get("status_id"), {}).get("group", "active")
        for lead in leads
    ]
    return [
        {"name": name, "count": sum(1 for g in lead_groups if g in groups)}
        for name, groups in ATTR_FUNNEL
    ]


def compute_cohort_table(leads, statuses):
    """Weekly cohort conversion table (Mon–Sun cohorts by creation date).

    For each cohort: how many leads from that week are currently at each
    attributed funnel stage. Conversion = stage_i / stage_{i-1}.
    Cohorts started < 14 days ago are flagged as immature (funnel not yet settled).
    """
    tz_msk = datetime.timezone(datetime.timedelta(hours=3))
    today   = datetime.datetime.now(tz_msk).date()
    immature_cutoff = today - datetime.timedelta(days=14)

    stage_names  = [name   for name, _      in ATTR_FUNNEL]
    stage_groups = [groups for _,    groups in ATTR_FUNNEL]

    # Group leads by Monday of their creation week
    week_leads = defaultdict(list)
    for lead in leads:
        ts = lead.get("created_at")
        if not ts:
            continue
        d = datetime.datetime.fromtimestamp(ts, tz=tz_msk).date()
        monday = d - datetime.timedelta(days=d.weekday())
        week_leads[monday].append(lead)

    cohort_start = datetime.date(2026, 6, 1)
    sorted_weeks = [w for w in sorted(week_leads.keys()) if w >= cohort_start]

    def stage_counts(lead_list):
        groups = [statuses.get(l.get("status_id"), {}).get("group", "active")
                  for l in lead_list]
        return [sum(1 for g in groups if g in sg) for sg in stage_groups]

    cohort_counts = {}
    for monday in sorted_weeks:
        sunday = monday + datetime.timedelta(days=6)
        label  = f"{monday.strftime('%d.%m')}–{sunday.strftime('%d.%m')}"
        cohort_counts[label] = stage_counts(week_leads[monday])

    week_labels = []
    immature    = set()
    for monday in sorted_weeks:
        sunday = monday + datetime.timedelta(days=6)
        label  = f"{monday.strftime('%d.%m')}–{sunday.strftime('%d.%m')}"
        week_labels.append(label)
        if monday > immature_cutoff:
            immature.add(label)

    # Overall totals (all leads regardless of week)
    totals = stage_counts(leads)

    return {
        "weeks":    week_labels,
        "immature": list(immature),
        "stages":   stage_names,
        "counts":   cohort_counts,   # {week_label: [count_per_stage]}
        "totals":   totals,
    }

def compute_conversion_by_day(leads, statuses, tz_msk, start_date, today):
    """Cumulative conversion Взято→Контакт grouped by Mon–Sun week of lead creation date."""
    week_vzv = Counter()   # week_monday -> leads that reached "Взято в работу" or higher
    week_kon = Counter()   # week_monday -> leads that reached "Контакт установлен" or higher

    for lead in leads:
        grp = statuses.get(lead.get("status_id"), {}).get("group", "active")
        pos = FUNNEL_POS.get(grp, 0)
        if pos < 4:          # didn't reach "Взято в работу"
            continue
        created_ts = lead.get("created_at")
        if not created_ts:
            continue
        lead_date = datetime.datetime.fromtimestamp(created_ts, tz=tz_msk).date()
        if lead_date < start_date or lead_date > today:
            continue
        # Monday of the creation week
        week_mon = lead_date - datetime.timedelta(days=lead_date.weekday())
        week_vzv[week_mon] += 1
        if pos >= 5:
            week_kon[week_mon] += 1

    # Build sorted list of weeks from start_date's Monday up to today's Monday
    first_mon = start_date - datetime.timedelta(days=start_date.weekday())
    weeks = []
    w = first_mon
    while w <= today:
        weeks.append(w)
        w += datetime.timedelta(weeks=1)

    week_labels = [f"{w.strftime('%d.%m')}–{(w + datetime.timedelta(days=6)).strftime('%d.%m')}" for w in weeks]
    vzv_vals = [week_vzv.get(w, 0) for w in weeks]
    kon_pct  = [
        round(week_kon.get(w, 0) / week_vzv[w] * 100) if week_vzv.get(w) else 0
        for w in weeks
    ]
    return week_labels, vzv_vals, kon_pct


def fetch_overdue_tasks(filtered_lead_ids):
    """Fetch overdue tasks, counting only those linked to leads from the target source."""
    now_ts = int(datetime.datetime.utcnow().timestamp())
    try:
        tasks = []
        page = 1
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
    except Exception:
        return {}
    counts = Counter()
    for t in tasks:
        # Only count tasks linked to leads from "Анкета перезаписи 06.2026"
        if t.get("entity_type") == "leads" and t.get("entity_id") not in filtered_lead_ids:
            continue
        uid = t.get("responsible_user_id")
        if uid in MANAGERS:
            counts[uid] += 1
    return dict(counts)

# ── Phone prefix → country mapping ───────────────────────────────────────────

# Ordered longest-prefix-first so greedy match works correctly
PHONE_COUNTRIES = [
    # CIS & post-Soviet
    ("7700", "Казахстан"), ("7701", "Казахстан"), ("7702", "Казахстан"),
    ("7705", "Казахстан"), ("7706", "Казахстан"), ("7707", "Казахстан"),
    ("7708", "Казахстан"), ("7709", "Казахстан"),
    ("7710", "Казахстан"), ("7711", "Казахстан"), ("7712", "Казахстан"),
    ("7713", "Казахстан"), ("7714", "Казахстан"), ("7715", "Казахстан"),
    ("7716", "Казахстан"), ("7717", "Казахстан"), ("7718", "Казахстан"),
    ("7719", "Казахстан"), ("7721", "Казахстан"), ("7722", "Казахстан"),
    ("7723", "Казахстан"), ("7724", "Казахстан"), ("7725", "Казахстан"),
    ("7726", "Казахстан"), ("7727", "Казахстан"), ("7728", "Казахстан"),
    ("7729", "Казахстан"),
    ("7",    "Россия"),
    ("380",  "Украина"),
    ("375",  "Беларусь"),
    ("374",  "Армения"),
    ("994",  "Азербайджан"),
    ("998",  "Узбекистан"),
    ("996",  "Кыргызстан"),
    ("992",  "Таджикистан"),
    ("993",  "Туркменистан"),
    ("995",  "Грузия"),
    ("373",  "Молдова"),
    # Europe & other popular
    ("972",  "Израиль"),
    ("971",  "ОАЭ"),
    ("90",   "Турция"),
    ("49",   "Германия"),
    ("44",   "Великобритания"),
    ("33",   "Франция"),
    ("39",   "Италия"),
    ("34",   "Испания"),
    ("48",   "Польша"),
    ("420",  "Чехия"),
    ("36",   "Венгрия"),
    ("40",   "Румыния"),
    ("31",   "Нидерланды"),
    ("32",   "Бельгия"),
    ("41",   "Швейцария"),
    ("43",   "Австрия"),
    ("46",   "Швеция"),
    ("47",   "Норвегия"),
    ("45",   "Дания"),
    ("358",  "Финляндия"),
    ("352",  "Люксембург"),
    ("386",  "Словения"),
    ("385",  "Хорватия"),
    ("381",  "Сербия"),
    ("370",  "Литва"),
    ("371",  "Латвия"),
    ("372",  "Эстония"),
    ("966",  "Саудовская Аравия"),
    ("974",  "Катар"),
    ("965",  "Кувейт"),
    ("62",   "Индонезия"),
    ("60",   "Малайзия"),
    ("65",   "Сингапур"),
    ("66",   "Таиланд"),
    ("84",   "Вьетнам"),
    ("82",   "Южная Корея"),
    ("81",   "Япония"),
    ("86",   "Китай"),
    ("91",   "Индия"),
    # Canada area codes (must come before the generic "1" = USA entry)
    ("1204", "Канада"), ("1226", "Канада"), ("1236", "Канада"), ("1249", "Канада"),
    ("1250", "Канада"), ("1289", "Канада"), ("1306", "Канада"), ("1343", "Канада"),
    ("1354", "Канада"), ("1365", "Канада"), ("1367", "Канада"), ("1368", "Канада"),
    ("1403", "Канада"), ("1416", "Канада"), ("1418", "Канада"), ("1431", "Канада"),
    ("1437", "Канада"), ("1438", "Канада"), ("1450", "Канада"), ("1468", "Канада"),
    ("1506", "Канада"), ("1514", "Канада"), ("1519", "Канада"), ("1548", "Канада"),
    ("1579", "Канада"), ("1581", "Канада"), ("1587", "Канада"), ("1604", "Канада"),
    ("1613", "Канада"), ("1639", "Канада"), ("1647", "Канада"), ("1672", "Канада"),
    ("1705", "Канада"), ("1709", "Канада"), ("1742", "Канада"), ("1778", "Канада"),
    ("1780", "Канада"), ("1782", "Канада"), ("1807", "Канада"), ("1819", "Канада"),
    ("1825", "Канада"), ("1867", "Канада"), ("1873", "Канада"), ("1902", "Канада"),
    ("1905", "Канада"),
    ("1",    "США"),
    ("55",   "Бразилия"),
    ("52",   "Мексика"),
    ("54",   "Аргентина"),
    ("61",   "Австралия"),
]

def phone_to_country(raw_phone: str) -> str:
    """Normalise phone to digits-only and match longest prefix."""
    digits = "".join(c for c in raw_phone if c.isdigit())
    # Strip leading zeros (some CRMs store 00380... instead of 380...)
    digits = digits.lstrip("0") or digits
    for prefix, country in PHONE_COUNTRIES:
        if digits.startswith(prefix):
            return country
    return "Не определено"


def fetch_contacts_phones(contact_ids: list) -> dict:
    """Batch-fetch contacts by IDs and return {contact_id: phone_or_None}."""
    result = {}
    batch_size = 250
    for i in range(0, len(contact_ids), batch_size):
        chunk = contact_ids[i : i + batch_size]
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

    print("Resolving source enum IDs…")
    source_enum_ids = resolve_source_enum_ids()
    print(f"  Tracking sources: { {v: k for k,v in source_enum_ids.items()} }")

    print("Fetching leads…")
    leads = fetch_filtered_leads(statuses, source_enum_ids)
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

    # Split leads by source type
    leads_prereg     = [l for l in leads if l.get("_source") == "prereg"]
    leads_web_order  = [l for l in leads if l.get("_source") == "web_order"]
    leads_web_prepay = [l for l in leads if l.get("_source") == "web_prepay"]
    leads_web        = leads_web_order + leads_web_prepay

    # Helper: compute mgr_viz for any lead subset
    def _mgr_viz(subset):
        mv = defaultdict(Counter)
        for lead in subset:
            uid = lead.get("responsible_user_id")
            if uid not in MANAGERS:
                continue
            sid = lead.get("status_id")
            grp = statuses.get(sid, {}).get("group", "active")
            vg  = VIZ_GROUP.get(grp, "active")
            mv[uid][vg] += 1
        return {str(uid): dict(mv[uid]) for uid in MANAGERS}

    # Per-manager (managers only) using viz groups — all sources combined
    mgr_viz         = defaultdict(Counter)
    mgr_viz_prereg  = defaultdict(Counter)
    mgr_viz_web     = defaultdict(Counter)
    for lead in leads:
        uid = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        sid = lead.get("status_id")
        grp = statuses.get(sid, {}).get("group", "active")
        vg  = VIZ_GROUP.get(grp, "active")
        mgr_viz[uid][vg] += 1
        src = lead.get("_source", "prereg")
        if src == "prereg":
            mgr_viz_prereg[uid][vg] += 1
        else:
            mgr_viz_web[uid][vg] += 1

    mgr_viz_prereg_d = {str(uid): dict(mgr_viz_prereg[uid]) for uid in MANAGERS}
    mgr_viz_web_d    = {str(uid): dict(mgr_viz_web[uid])    for uid in MANAGERS}

    # ── Webinar funnel metrics ─────────────────────────────────────────────────
    prereg_converted     = 0   # closed → "Оставил Заказ"
    prereg_disqualified  = 0   # closed → invalid reason (Дубль, спам…)
    prereg_real_lost     = 0   # closed → other reason (genuine refusal)

    for lead in leads_prereg:
        grp = statuses.get(lead.get("status_id"), {}).get("group", "")
        if grp != "lost":
            continue
        reason = None
        for cf in (lead.get("custom_fields_values") or []):
            if cf.get("field_id") == REASON_FIELD_ID:
                vals = cf.get("values") or []
                if vals:
                    reason = vals[0].get("value")
        if reason == CONVERT_REASON:
            prereg_converted += 1
        elif reason in INVALID_REASONS:
            prereg_disqualified += 1
        else:
            prereg_real_lost += 1

    web_order_total  = len(leads_web_order)
    web_prepay_total = len(leads_web_prepay)
    web_order_sales  = sum(
        1 for l in leads_web_order
        if statuses.get(l.get("status_id"), {}).get("group") == "sale"
    )
    web_prepay_sales = sum(
        1 for l in leads_web_prepay
        if statuses.get(l.get("status_id"), {}).get("group") == "sale"
    )
    web_total_sales  = web_order_sales + web_prepay_sales

    print("Fetching overdue tasks…")
    filtered_lead_ids = {lead["id"] for lead in leads if lead.get("id")}
    overdue = fetch_overdue_tasks(filtered_lead_ids)

    print("Fetching contacts for country distribution…")
    # Collect unique contact IDs from all leads (leads API returns _embedded.contacts)
    contact_to_lead = {}  # contact_id -> lead_id (first seen)
    for lead in leads:
        for c in (lead.get("_embedded", {}).get("contacts") or []):
            cid = c.get("id")
            if cid and cid not in contact_to_lead:
                contact_to_lead[cid] = lead["id"]
    phones_map = fetch_contacts_phones(list(contact_to_lead.keys()))

    # Build lead_id -> country for all further country analytics
    lead_to_country = {}
    for cid, phone in phones_map.items():
        lid = contact_to_lead.get(cid)
        if lid:
            lead_to_country[lid] = phone_to_country(phone) if phone else "Не определено"
    for lead in leads:
        if lead["id"] not in lead_to_country:
            lead_to_country[lead["id"]] = "Не определено"

    country_counts = Counter(lead_to_country.values())

    # Top-10 countries list (used for all country charts).
    # "Не определено" is treated as part of the "tail" bucket so it merges cleanly.
    TOP_N    = 10
    REST_LBL = "Прочие"
    # Separate "Не определено" from real countries before ranking
    undefined_count = country_counts.pop("Не определено", 0)
    most_common = country_counts.most_common()  # real countries only
    top_real = most_common[:TOP_N]
    rest_real = most_common[TOP_N:]
    rest_total = sum(v for _, v in rest_real) + undefined_count
    top_labels = [c for c, _ in top_real]
    top_values = [v for _, v in top_real]
    if rest_total:
        top_labels.append(REST_LBL)
        top_values.append(rest_total)
    country_labels = top_labels
    country_values = top_values
    # Restore counter for other uses
    if undefined_count:
        country_counts["Не определено"] = undefined_count
    rest_lbl = REST_LBL if rest_total else None
    top_country_set = set(top_labels)

    def _country_labels_values(lead_subset):
        """Top-N country breakdown for any subset of leads."""
        counts = Counter(lead_to_country.get(l["id"], "Не определено") for l in lead_subset)
        undef = counts.pop("Не определено", 0)
        common = counts.most_common()
        top = common[:TOP_N]
        rest_c = common[TOP_N:]
        rt = sum(v for _, v in rest_c) + undef
        lbs = [c for c, _ in top]
        vls = [v for _, v in top]
        if rt:
            lbs.append(REST_LBL)
            vls.append(rt)
        return lbs, vls

    country_labels_prereg, country_values_prereg = _country_labels_values(leads_prereg)
    country_labels_web,    country_values_web    = _country_labels_values(leads_web)

    # ── Status distribution by country (100% stacked) ─────────────────────────
    # Simplified display groups for the stacked bar
    CSTAT_GROUPS = [
        ("Входящие/Новые", {"incoming", "new_lead", "om"}),
        ("В работе",       {"in_work", "contact", "qualified"}),
        ("Оффер/Отложен",  {"offer", "delayed"}),
        ("Продажи",        {"sale", "invoiced"}),
        ("НДЗ",            {"ndz"}),
        ("Потеряно",       {"lost"}),
    ]
    CSTAT_COLORS = ["#74b9ff", "#00cec9", "#f5a623", "#6ab04c", "#fdcb6e", "#636e72"]

    def _build_cstat_datasets(lead_list):
        """Build cstat datasets for a given list of leads (reusable for weekly slices)."""
        raw = defaultdict(Counter)
        for lead in lead_list:
            country = lead_to_country.get(lead["id"], "Не определено")
            c_key = country if country in top_country_set else REST_LBL
            grp = statuses.get(lead.get("status_id"), {}).get("group", "")
            for gname, gset in CSTAT_GROUPS:
                if grp in gset:
                    raw[c_key][gname] += 1
                    break
        return [
            {"label": gname, "color": color,
             "data": [raw[c].get(gname, 0) for c in country_labels]}
            for (gname, _), color in zip(CSTAT_GROUPS, CSTAT_COLORS)
        ]

    # All-time datasets
    cstat_datasets = _build_cstat_datasets(leads)

    # Weekly slices — same country_labels axis so weeks are comparable
    cstat_tz = datetime.timezone(datetime.timedelta(hours=3))
    cstat_start = datetime.date(2026, 6, 6)
    week_leads_map = defaultdict(list)
    for lead in leads:
        ts = lead.get("created_at")
        if not ts:
            continue
        ld = datetime.datetime.fromtimestamp(ts, tz=cstat_tz).date()
        if ld < cstat_start:
            continue
        mon = ld - datetime.timedelta(days=ld.weekday())
        sun = mon + datetime.timedelta(days=6)
        wlbl = f"{mon.strftime('%d.%m')}–{sun.strftime('%d.%m')}"
        week_leads_map[wlbl].append(lead)

    cstat_by_week = {"Все время": cstat_datasets}
    for wlbl in sorted(week_leads_map):
        cstat_by_week[wlbl] = _build_cstat_datasets(week_leads_map[wlbl])

    # ── Manager × country heatmap ──────────────────────────────────────────────
    # Top-5 countries (real ones, not rest bucket) for column headers
    TOP_MGR_COUNTRIES = 10
    mgr_country_cols = [c for c in country_labels if c != rest_lbl][:TOP_MGR_COUNTRIES]
    if rest_lbl:
        mgr_country_cols.append(REST_LBL)

    mgr_country_raw = defaultdict(Counter)
    for lead in leads:
        uid = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        country = lead_to_country.get(lead["id"], "Не определено")
        known_cols = {c for c in mgr_country_cols if c != REST_LBL}
        if country in known_cols:
            mgr_country_raw[str(uid)][country] += 1
        else:
            mgr_country_raw[str(uid)][REST_LBL] += 1

    mgr_country_data = {
        str(uid): {c: mgr_country_raw[str(uid)].get(c, 0) for c in mgr_country_cols}
        for uid in MANAGERS
    }

    def _build_mgr_country_data(subset):
        raw = defaultdict(Counter)
        known = {c for c in mgr_country_cols if c != REST_LBL}
        for lead in subset:
            uid = lead.get("responsible_user_id")
            if uid not in MANAGERS:
                continue
            country = lead_to_country.get(lead["id"], "Не определено")
            if country in known:
                raw[str(uid)][country] += 1
            else:
                raw[str(uid)][REST_LBL] += 1
        return {
            str(uid): {c: raw[str(uid)].get(c, 0) for c in mgr_country_cols}
            for uid in MANAGERS
        }

    mgr_country_by_week = {"Все время": mgr_country_data}
    for wlbl in sorted(week_leads_map):
        mgr_country_by_week[wlbl] = _build_mgr_country_data(week_leads_map[wlbl])


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
    # Remove Экскурсия from funnel chart (redundant with sale category)
    sorted_statuses = [s for s in sorted_statuses if s.get("name") != "Экскурсия"]

    # Cumulative funnel (bar chart)
    cumulative_funnel = compute_cumulative_funnel(leads, statuses)

    # Cohort conversion table (weekly)
    cohort_table = compute_cohort_table(leads, statuses)

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

    # Capital order for display — "Не указан" at the end for leads with empty field
    NO_CAPITAL = "Не указан"
    CAPITAL_ORDER = ["$0-5,000", "до $5,000", "$5,000-50,000", "$50,000-100,000",
                     "$100,000-500,000", "$500,000-1,000,000", "$1,000,000+",
                     "Неизвестно", NO_CAPITAL]

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
        # Leads with no capital value go into the "Не указан" bucket
        effective_cap = cap_val if cap_val else NO_CAPITAL
        capital_counts[effective_cap] += 1
        created_ts = lead.get("created_at")
        if created_ts:
            lead_date = datetime.datetime.fromtimestamp(created_ts, tz=tz_msk).date()
            day_key = lead_date.strftime("%d.%m")
            if day_key in daily_capital:
                daily_capital[day_key][effective_cap] += 1
        ready_counts[rdy_val if rdy_val else "Не ответил на вопрос"] += 1

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

    # Closure reasons for lost leads
    # Exclude INVALID_REASONS (дубли, спам…) and TEST_REASON; keep CONVERT_REASON visible
    reason_counts = Counter()
    for lead in leads:
        grp = statuses.get(lead.get("status_id"), {}).get("group", "")
        if grp != "lost":
            continue
        for cf in (lead.get("custom_fields_values") or []):
            if cf.get("field_id") == REASON_FIELD_ID:
                vals = cf.get("values") or []
                if vals:
                    reason = vals[0].get("value", "?")
                    if reason not in INVALID_REASONS and reason != TEST_REASON:
                        reason_counts[reason] += 1

    # Sort by count descending
    reason_labels = [r for r, _ in reason_counts.most_common()]
    reason_values = [reason_counts[r] for r in reason_labels]

    # Sales by tariff (Тариф field, won deals only)
    TARIFF_FIELD_ID = 1315345
    tariff_counts = Counter()
    for lead in leads:
        grp = statuses.get(lead.get("status_id"), {}).get("group", "")
        if grp != "sale":
            continue
        tariff_val = None
        for cf in (lead.get("custom_fields_values") or []):
            if cf.get("field_id") == TARIFF_FIELD_ID:
                vals = cf.get("values") or []
                if vals:
                    tariff_val = vals[0].get("value")
        tariff_counts[tariff_val or "Не указан"] += 1
    tariff_labels = [t for t, _ in tariff_counts.most_common()]
    tariff_values = [tariff_counts[t] for t in tariff_labels]

    # Per-manager revenue (sum of prices of won deals) and sales count
    mgr_revenue     = defaultdict(int)
    mgr_sales_cnt   = defaultdict(int)
    for lead in leads:
        uid = lead.get("responsible_user_id")
        if uid not in MANAGERS:
            continue
        grp = statuses.get(lead.get("status_id"), {}).get("group", "")
        if grp == "sale":
            mgr_revenue[uid]   += lead.get("price") or 0
            mgr_sales_cnt[uid] += 1

    # Sort by revenue descending
    sorted_revenue = sorted(mgr_revenue.items(), key=lambda x: x[1], reverse=True)
    revenue_mgr_ids  = [str(uid) for uid, _ in sorted_revenue]
    revenue_values   = [rev for _, rev in sorted_revenue]

    # Per-manager sales count (same order as revenue)
    mgr_sales_count = {str(uid): mgr_sales_cnt.get(uid, 0) for uid in MANAGERS}

    # Per-manager avg ticket
    mgr_avg_price = {
        str(uid): round(mgr_revenue[uid] / mgr_sales_cnt[uid]) if mgr_sales_cnt.get(uid) else 0
        for uid in MANAGERS
    }

    # Overall avg ticket
    total_sales_count = sum(mgr_sales_cnt.values())
    avg_price = round(total_price / total_sales_count) if total_sales_count else 0

    # Per-manager conversion: Взято в работу → Продажи
    # Denominator uses same ATTR_FUNNEL logic as "Взято в работу" stage
    INWORK_GROUPS = frozenset({"in_work", "contact", "qualified",
                                "offer", "delayed", "invoiced", "sale",
                                "ndz", "lost"})
    mgr_conv_data = {}
    for uid in MANAGERS:
        inwork = sum(
            1 for l in leads
            if l.get("responsible_user_id") == uid
            and statuses.get(l.get("status_id"), {}).get("group", "") in INWORK_GROUPS
        )
        sales = sum(
            1 for l in leads
            if l.get("responsible_user_id") == uid
            and statuses.get(l.get("status_id"), {}).get("group", "") == "sale"
        )
        mgr_conv_data[str(uid)] = {
            "inwork": inwork,
            "sales":  sales,
            "pct":    round(sales / inwork * 100, 1) if inwork else 0,
        }

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
        "mgr_viz_prereg":   mgr_viz_prereg_d,
        "mgr_viz_web":      mgr_viz_web_d,
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
        "reason_labels":    reason_labels,
        "reason_values":    reason_values,
        "country_labels":         country_labels,
        "country_values":         country_values,
        "country_labels_prereg":  country_labels_prereg,
        "country_values_prereg":  country_values_prereg,
        "country_labels_web":     country_labels_web,
        "country_values_web":     country_values_web,
        "cstat_datasets":   cstat_datasets,
        "cstat_by_week":    cstat_by_week,
        "mgr_country_cols":     mgr_country_cols,
        "mgr_country_data":     mgr_country_data,
        "mgr_country_by_week":  mgr_country_by_week,
        "tariff_labels":    tariff_labels,
        "tariff_values":    tariff_values,
        "revenue_mgr_ids":  revenue_mgr_ids,
        "revenue_values":   revenue_values,
        "mgr_sales_count":  mgr_sales_count,
        "mgr_avg_price":    mgr_avg_price,
        "avg_price":        avg_price,
        "mgr_conv":         mgr_conv_data,
        "cumulative_funnel": cumulative_funnel,
        "cohort_table":      cohort_table,
        # Webinar funnel
        "prereg_total":       len(leads_prereg),
        "prereg_converted":   prereg_converted,
        "prereg_disqualified": prereg_disqualified,
        "prereg_real_lost":   prereg_real_lost,
        "web_order_total":    web_order_total,
        "web_prepay_total":   web_prepay_total,
        "web_total_sales":    web_total_sales,
    }

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ОП Dashboard — Анкета перезаписи 06.2026</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<script>
// Unregister datalabels globally — we register it per-chart where needed
if(typeof ChartDataLabels !== 'undefined') {{
  Chart.unregister(ChartDataLabels);
}}
</script>
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
<div style="display:flex;align-items:center;gap:16px;margin-bottom:28px">
  <p class="meta" style="margin:0">Источник: amoCRM simmihur &nbsp;·&nbsp; Обновлено: {updated_at}</p>
  <button id="refreshBtn" onclick="triggerRefresh()" style="background:#4f8ef7;color:#fff;border:none;border-radius:6px;padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px">
    <span id="refreshIcon">↻</span> <span id="refreshText">Обновить данные</span>
  </button>
</div>
<script>
function triggerRefresh() {{
  const btn  = document.getElementById('refreshBtn');
  const icon = document.getElementById('refreshIcon');
  const text = document.getElementById('refreshText');
  btn.disabled = true;
  btn.style.opacity = '0.6';
  icon.style.display = 'inline-block';
  icon.style.animation = 'spin 1s linear infinite';
  text.textContent = 'Запускаю обновление…';
  fetch('https://api.github.com/repos/Admin-web3a/op-dashboard/actions/workflows/daily.yml/dispatches', {{
    method: 'POST',
    headers: {{
      'Authorization': 'Bearer ' + 'github_pat_11B5MIWKI0FeFZwGIvGnUW_' + 'k4r2oBZYBtLbjS5zKQ8tihNdCXgble7pSUn7ToJbVrg7O3G2T7V1NzRS5FV',
      'Content-Type': 'application/json',
    }},
    body: JSON.stringify({{ref: 'main'}}),
  }})
  .then(function(r) {{
    if(r.status === 204) {{
      icon.style.animation = '';
      icon.textContent = '✓';
      text.textContent = 'Запущено! Обновите страницу через 3 мин.';
      btn.style.background = '#6ab04c';
      btn.style.opacity = '1';
    }} else {{
      throw new Error('status ' + r.status);
    }}
  }})
  .catch(function(e) {{
    icon.style.animation = '';
    icon.textContent = '✕';
    text.textContent = 'Ошибка: ' + e.message;
    btn.style.background = '#eb4d4b';
    btn.style.opacity = '1';
    btn.disabled = false;
  }});
}}
</script>
<style>
@keyframes spin {{ from {{transform:rotate(0deg)}} to {{transform:rotate(360deg)}} }}
</style>

<div class="stats">
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Всего лидов</div></div>
  <div class="stat accent"><div class="stat-value">{active}</div><div class="stat-label">В работе</div></div>
  <div class="stat orange"><div class="stat-value">{ndz}</div><div class="stat-label">НДЗ</div></div>
  <div class="stat blue"><div class="stat-value">{offer_ozv}</div><div class="stat-label">Оффер озвучен</div></div>
  <div class="stat blue"><div class="stat-value">{delayed}</div><div class="stat-label">Отложенный спрос</div></div>
  <div class="stat purple"><div class="stat-value">{invoiced}</div><div class="stat-label">Выставлен счет</div></div>
  <div class="stat green"><div class="stat-value">{sales}</div><div class="stat-label">Продажи</div></div>
  <div class="stat"><div class="stat-value">{conv_pct}%</div><div class="stat-label">Конверсия в продажу</div></div>
  <div class="stat" style="min-width:180px"><div class="stat-value" style="font-size:20px">{price}</div><div class="stat-label">Сумма сделок, ₽</div></div>
  <div class="stat" style="min-width:180px"><div class="stat-value" style="font-size:20px">{avg_price}</div><div class="stat-label">Средний чек, ₽</div></div>
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

<h2>Кумулятивная воронка (атрибутированная)</h2>
<div class="chart-card" style="height:360px"><canvas id="cumFunnelChart"></canvas></div>

<h2>Конверсия по неделям (когортный анализ)</h2>
<p style="color:#8b8fa8;font-size:12px;margin:-10px 0 14px">Лиды сгруппированы по дате создания (неделя пн–вс). * — незрелые когорты (&lt;14 дней), конверсия занижена.</p>
<div style="overflow-x:auto">
<table id="cohortTable" style="min-width:600px"></table>
</div>

<h2>Конверсия: Взято в работу → Контакт установлен</h2>
<div class="chart-card" style="height:320px"><canvas id="convFunnelChart"></canvas></div>

<h2>Лиды по менеджерам</h2>
<div id="mgrSourceBtns" style="display:flex;gap:8px;margin-bottom:12px"></div>
<div class="chart-card" style="height:600px"><canvas id="mgrChart"></canvas></div>

<h2>Просроченные задачи по менеджерам</h2>
<div class="chart-card" style="height:600px"><canvas id="overdueChart"></canvas></div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start;margin-bottom:16px">
  <div>
    <h2>Выручка по менеджерам</h2>
    <div class="chart-card" style="height:360px"><canvas id="revenueChart"></canvas></div>
  </div>
  <div>
    <h2>Количество продаж по менеджерам</h2>
    <div class="chart-card" style="height:360px"><canvas id="mgrSalesChart"></canvas></div>
  </div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start">
  <div>
    <h2>Конверсия Взято в работу → Продажи</h2>
    <div class="chart-card" style="height:360px"><canvas id="mgrConvChart"></canvas></div>
  </div>
  <div>
    <h2>Средний чек по менеджерам</h2>
    <div class="chart-card" style="height:360px"><canvas id="mgrAvgChart"></canvas></div>
  </div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start">
  <div>
    <h2>Продажи по тарифам</h2>
    <div class="chart-card" style="height:320px"><canvas id="tariffChart"></canvas></div>
  </div>
  <div>
    <h2>Лиды по странам</h2>
    <div id="countrySourceBtns" style="display:flex;gap:8px;margin-bottom:12px"></div>
    <div class="chart-card" style="height:380px"><canvas id="countryChart"></canvas></div>
  </div>
</div>

<h2>Статусы по странам</h2>
<div id="cstatWeekBtns" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px"></div>
<div class="chart-card" style="height:420px"><canvas id="countryStatusChart"></canvas></div>

<h2>Лиды по менеджерам × странам</h2>
<div id="mgrCountryWeekBtns" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px"></div>
<div class="chart-card" style="overflow-x:auto;padding:16px">
  <table id="mgrCountryTable" style="width:100%;border-collapse:collapse;font-size:13px"></table>
</div>

<h2>Воронка вебинара 06.26</h2>
<div id="webinarFunnel" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:16px">
  <!-- filled by JS -->
</div>

<h2>Причины закрытия сделок</h2>
<div class="chart-card" style="height:320px"><canvas id="reasonChart"></canvas></div>

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
const VCOLORS = {{incoming:"#74b9ff",new_lead:"#0984e3",om:"#6c5ce7",in_work:"#00cec9",contact:"#ffd32a",qualified:"#ff6b81",ndz:"#f5a623",offer:"#eb4d4b",delayed:"#a29bfe",sale:"#6ab04c",lost:"#eb4d4b"}};
const VLABELS = {{incoming:"Входящие",new_lead:"Новый лид",om:"ОМ назначен",in_work:"Взято в работу",contact:"Контакт установлен",qualified:"Квалифицирован",ndz:"НДЗ",offer:"Оффер озвучен",delayed:"Отложен",sale:"Продажи+"}};
const VORDER  = ["incoming","new_lead","om","in_work","contact","qualified","ndz","offer","delayed","sale"];
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


// Attributed funnel
(function(){{
  const stages = DATA.cumulative_funnel;
  const topVal = stages[0] ? stages[0].count : 1;
  const palette = ["#4f8ef7","#00cec9","#ffd32a","#ff6b81","#7ed6df","#a29bfe","#6ab04c"];
  new Chart(document.getElementById("cumFunnelChart"),{{
    type:"bar",
    data:{{
      labels: stages.map(s=>s.name),
      datasets:[{{
        label:"Лидов",
        data: stages.map(s=>s.count),
        backgroundColor: stages.map((_,i)=>palette[i]||"#4f8ef7"),
        borderRadius:3,
      }}]
    }},
    options:{{
      indexAxis:"y",
      maintainAspectRatio:false,
      plugins:{{
        legend:{{display:false}},
        tooltip:{{callbacks:{{
          label:function(c){{
            const i = c.dataIndex;
            const pctTop = topVal ? Math.round(c.raw/topVal*100) : 0;
            const prev = i > 0 ? stages[i-1].count : topVal;
            const pctPrev = prev ? Math.round(c.raw/prev*100) : 0;
            const lines = [` ${{fmt(c.raw)}} лидов (${{pctTop}}% от входящих)`];
            if(i > 0) lines.push(` Конверсия с предыдущего: ${{pctPrev}}%`);
            return lines;
          }}
        }}}}
      }},
      scales:{{
        x:{{beginAtZero:true,ticks:{{color:"#e8eaf0"}},grid:{{color:"#2a2d3a"}}}},
        y:{{ticks:{{color:"#e8eaf0",font:{{size:13}}}},grid:{{color:"#2a2d3a"}}}}
      }}
    }}
  }});
}})();

// Cohort conversion table
(function(){{
  const ct = DATA.cohort_table;
  if(!ct||!ct.weeks||!ct.weeks.length) return;
  const immSet = new Set(ct.immature);
  const weeks  = ct.weeks;
  const stages = ct.stages;
  const counts = ct.counts;
  const totals = ct.totals;

  function convColor(pct){{
    if(pct>=70) return '#6ab04c';
    if(pct>=40) return '#f5a623';
    return '#eb4d4b';
  }}
  function barCell(cnt, prev, immature, bgStyle){{
    const pct = prev>0 ? Math.round(cnt/prev*100) : 0;
    const col = convColor(pct);
    const op  = immature ? 'opacity:0.55;' : '';
    const bg  = bgStyle  ? bgStyle         : '';
    return '<td style="padding:6px 12px;' + op + bg + '">'
      + '<div style="display:flex;align-items:center;gap:7px">'
      + '<div style="width:54px;height:7px;background:#2a2d3a;border-radius:3px;flex-shrink:0">'
      + '<div style="width:' + pct + '%;height:100%;background:' + col + ';border-radius:3px"></div>'
      + '</div>'
      + '<span style="font-size:12px;color:' + col + ';font-weight:600">' + pct + '%</span>'
      + '</div></td>';
  }}

  // Header
  let html = '<thead><tr><th style="min-width:180px">Этап / Конверсия</th>';
  weeks.forEach(function(w){{
    const imm = immSet.has(w);
    html += '<th style="text-align:center;' + (imm?'opacity:0.6':'') + '">' + w + (imm?' *':'') + '</th>';
  }});
  html += '<th style="text-align:center;background:#1a2e0a">Итого</th></tr></thead><tbody>';

  stages.forEach(function(stage, si){{
    // Count row
    html += '<tr style="border-top:2px solid #2a2d3a"><td style="font-weight:600;color:#e8eaf0;font-size:13px">' + stage + '</td>';
    weeks.forEach(function(w){{
      const cnt = ((counts[w]||[])[si])||0;
      const imm = immSet.has(w);
      html += '<td class="num" style="' + (imm?'opacity:0.6':'') + '">' + fmt(cnt) + '</td>';
    }});
    html += '<td class="num" style="font-weight:700;background:#1a1f0a">' + fmt(totals[si]) + '</td></tr>';

    // Conversion row (skip first stage — no previous stage)
    if(si > 0){{
      html += '<tr><td style="font-size:11px;color:#8b8fa8;padding-left:18px">&#8627; к предыдущему</td>';
      weeks.forEach(function(w){{
        const cnt  = ((counts[w]||[])[si])||0;
        const prev = ((counts[w]||[])[si-1])||0;
        html += barCell(cnt, prev, immSet.has(w), '');
      }});
      html += barCell(totals[si], totals[si-1], false, 'background:#1a1f0a;');
      html += '</tr>';
    }}
  }});

  html += '</tbody>';
  document.getElementById('cohortTable').innerHTML = html;
}})();

// Managers stacked
const mgrIds=Object.keys(DATA.mgr_viz).sort((a,b)=>{{
  const ta=Object.values(DATA.mgr_viz[a]).reduce((s,v)=>s+v,0);
  const tb=Object.values(DATA.mgr_viz[b]).reduce((s,v)=>s+v,0);
  return tb-ta;
}});
// Mgr chart with source toggle
(function(){{
  const srcMaps = {{
    all:    DATA.mgr_viz,
    prereg: DATA.mgr_viz_prereg,
    web:    DATA.mgr_viz_web,
  }};
  const srcLabels = {{all:"Все источники", prereg:"Предзапись", web:"Вебинар"}};
  let activeSrc = "all";

  const btnStyle = (a) =>
    "padding:5px 12px;border-radius:5px;border:none;cursor:pointer;font-size:12px;font-weight:600;" +
    (a ? "background:#4f8ef7;color:#fff;" : "background:#1e2a3a;color:#a0aec0;");

  const btnWrap = document.getElementById("mgrSourceBtns");
  const chart = new Chart(document.getElementById("mgrChart"), {{
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

  function renderMgrBtns() {{
    btnWrap.innerHTML = "";
    Object.keys(srcMaps).forEach(k => {{
      const btn = document.createElement("button");
      btn.textContent = srcLabels[k];
      btn.style.cssText = btnStyle(k === activeSrc);
      btn.onclick = function() {{
        activeSrc = k;
        const viz = srcMaps[k];
        chart.data.datasets.forEach((ds, i) => {{
          const g = VORDER[i];
          ds.data = mgrIds.map(id => (viz[id]||{{}})[g]||0);
        }});
        chart.update();
        renderMgrBtns();
      }};
      btnWrap.appendChild(btn);
    }});
  }}
  renderMgrBtns();
}})();

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
const capColors = {{"$0-5,000":"#eb4d4b","до $5,000":"#f5a623","$5,000-50,000":"#ffd32a","$50,000-100,000":"#6ab04c","$100,000-500,000":"#00cec9","$500,000-1,000,000":"#4f8ef7","$1,000,000+":"#a29bfe","Неизвестно":"#636e72","Не указан":"#3d4045"}};
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
      x:{{ticks:{{color:"#e8eaf0",maxRotation:20,font:{{size:11}}}},grid:{{color:"#1e2a3a"}}}},
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
      backgroundColor:["#eb4d4b","#f5a623","#ffd32a","#6ab04c","#00cec9","#4f8ef7","#a29bfe","#636e72","#3d4045"],
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
    labels:DATA.ready_labels.map(function(l){{
      if(l==="Супер_Я_готов") return "Готов сейчас";
      if(l==="Хочу_больше_узнать_про_программу") return "Хочу узнать больше";
      if(l==="Не ответил на вопрос") return "Не ответил на вопрос";
      return l;
    }}),
    datasets:[{{
      data:DATA.ready_values,
      backgroundColor:DATA.ready_labels.map(function(l){{
        if(l==="Супер_Я_готов") return "#6ab04c";
        if(l==="Хочу_больше_узнать_про_программу") return "#4f8ef7";
        if(l==="Не ответил на вопрос") return "#3d4045";
        return "#f5a623";
      }}),
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

// Revenue by manager
new Chart(document.getElementById("revenueChart"),{{
  type:"bar",
  data:{{
    labels:DATA.revenue_mgr_ids.map(id=>DATA.managers[id]||id),
    datasets:[{{
      label:"Выручка, ₽",
      data:DATA.revenue_values,
      backgroundColor:"#6ab04c",
      borderRadius:4,
    }}]
  }},
  options:{{
    maintainAspectRatio:false,
    plugins:{{
      legend:{{display:false}},
      tooltip:{{callbacks:{{
        label:function(c){{
          return " " + c.raw.toLocaleString("ru-RU") + " ₽";
        }}
      }}}}
    }},
    scales:{{
      x:{{ticks:{{color:"#e8eaf0",maxRotation:30}},grid:{{color:"#1e2a3a"}}}},
      y:{{beginAtZero:true,ticks:{{color:"#e8eaf0",callback:v=>v.toLocaleString("ru-RU")+" ₽"}},grid:{{color:"#1e2a3a"}}}}
    }}
  }}
}});

// Sales count by manager
(function(){{
  const sc = DATA.mgr_sales_count;
  const ids = Object.keys(sc)
    .filter(id => DATA.managers[id])
    .sort((a,b) => sc[b] - sc[a]);
  if(!ids.length) return;
  new Chart(document.getElementById('mgrSalesChart'), {{
    type: 'bar',
    data: {{
      labels: ids.map(id => DATA.managers[id]),
      datasets: [{{
        label: 'Продаж',
        data: ids.map(id => sc[id]),
        backgroundColor: '#4f8ef7',
        borderRadius: 4,
      }}]
    }},
    options: {{
      maintainAspectRatio: false,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{callbacks: {{label: function(c){{ return ' ' + c.raw + ' продаж'; }}}}}}
      }},
      scales: {{
        x: {{ticks: {{color: '#e8eaf0', maxRotation: 30}}, grid: {{color: '#1e2a3a'}}}},
        y: {{beginAtZero: true, ticks: {{color: '#e8eaf0', stepSize: 1}}, grid: {{color: '#1e2a3a'}}}}
      }}
    }}
  }});
}})();

// Manager conversion: Взято в работу → Продажи
(function(){{
  const conv = DATA.mgr_conv;
  const ids = Object.keys(conv)
    .filter(id => DATA.managers[id] && conv[id].inwork > 0)
    .sort((a,b) => conv[b].pct - conv[a].pct);
  if(!ids.length) return;
  const pcts   = ids.map(id => conv[id].pct);
  const labels = ids.map(id => DATA.managers[id]);
  const bgColors = pcts.map(p => p >= 5 ? '#6ab04c' : p >= 2 ? '#f5a623' : '#eb4d4b');
  new Chart(document.getElementById('mgrConvChart'), {{
    type: 'bar',
    data: {{
      labels: labels,
      datasets: [{{
        label: 'Конверсия %',
        data: pcts,
        backgroundColor: bgColors,
        borderRadius: 4,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      maintainAspectRatio: false,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{callbacks: {{
          label: function(c) {{
            const id = ids[c.dataIndex];
            const d = conv[id];
            return ' ' + c.raw + '% (' + d.sales + ' продаж из ' + d.inwork + ' в работе)';
          }}
        }}}}
      }},
      scales: {{
        x: {{
          beginAtZero: true, max: 10,
          ticks: {{color: '#e8eaf0', callback: function(v){{ return v + '%'; }}}},
          grid: {{color: '#2a2d3a'}}
        }},
        y: {{ticks: {{color: '#e8eaf0', font: {{size: 12}}}}, grid: {{color: '#2a2d3a'}}}}
      }}
    }}
  }});
}})();

// Avg ticket by manager
(function(){{
  const ap = DATA.mgr_avg_price;
  const ids = Object.keys(ap)
    .filter(id => DATA.managers[id] && ap[id] > 0)
    .sort((a,b) => ap[b] - ap[a]);
  if(!ids.length) return;
  new Chart(document.getElementById('mgrAvgChart'), {{
    type: 'bar',
    data: {{
      labels: ids.map(id => DATA.managers[id]),
      datasets: [{{
        label: 'Средний чек, ₽',
        data: ids.map(id => ap[id]),
        backgroundColor: '#a29bfe',
        borderRadius: 4,
      }}]
    }},
    options: {{
      maintainAspectRatio: false,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{callbacks: {{
          label: function(c){{ return ' ' + c.raw.toLocaleString('ru-RU') + ' ₽'; }}
        }}}}
      }},
      scales: {{
        x: {{ticks: {{color: '#e8eaf0', maxRotation: 30}}, grid: {{color: '#1e2a3a'}}}},
        y: {{
          beginAtZero: true,
          ticks: {{color: '#e8eaf0', callback: function(v){{ return v.toLocaleString('ru-RU') + ' ₽'; }}}},
          grid: {{color: '#1e2a3a'}}
        }}
      }}
    }}
  }});
}})();

// Sales by tariff — horizontal bar
(function(){{
  if(!DATA.tariff_labels.length) return;
  const palette = [
    '#4f8ef7','#6ab04c','#f5a623','#a29bfe','#fd79a8','#00cec9',
    '#e17055','#fdcb6e','#74b9ff','#55efc4','#b2bec3','#636e72'
  ];
  new Chart(document.getElementById('tariffChart'), {{
    type: 'bar',
    data: {{
      labels: DATA.tariff_labels,
      datasets: [{{
        label: 'Продаж',
        data: DATA.tariff_values,
        backgroundColor: DATA.tariff_labels.map((_,i) => palette[i % palette.length]),
        borderRadius: 4,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      maintainAspectRatio: false,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{callbacks: {{
          label: function(c) {{
            const total = DATA.tariff_values.reduce((a,b)=>a+b,0);
            return ' ' + c.raw + ' сделок (' + Math.round(c.raw/total*100) + '%)';
          }}
        }}}}
      }},
      scales: {{
        x: {{
          beginAtZero: true,
          ticks: {{color: '#e8eaf0', stepSize: 1}},
          grid: {{color: '#1e2a3a'}}
        }},
        y: {{ticks: {{color: '#e8eaf0', font: {{size: 12}}}}, grid: {{color: '#1e2a3a'}}}}
      }}
    }}
  }});
}})();

// Country distribution — horizontal bar with source toggle
(function(){{
  const srcData = {{
    all:    {{labels: DATA.country_labels,        values: DATA.country_values}},
    prereg: {{labels: DATA.country_labels_prereg, values: DATA.country_values_prereg}},
    web:    {{labels: DATA.country_labels_web,    values: DATA.country_values_web}},
  }};
  const srcLabels = {{all:"Все источники", prereg:"Предзапись", web:"Вебинар"}};
  let activeSrc = "all";

  const palette = [
    '#4f8ef7','#6ab04c','#f5a623','#a29bfe','#fd79a8','#00cec9',
    '#e17055','#fdcb6e','#74b9ff','#55efc4','#b2bec3','#636e72'
  ];

  const btnStyle = (a) =>
    "padding:5px 12px;border-radius:5px;border:none;cursor:pointer;font-size:12px;font-weight:600;" +
    (a ? "background:#4f8ef7;color:#fff;" : "background:#1e2a3a;color:#a0aec0;");

  function buildData(src) {{
    const lbs = (srcData[src].labels || []).slice().reverse();
    const vls = (srcData[src].values || []).slice().reverse();
    const tot = vls.reduce((a,b)=>a+b,0);
    return {{lbs, vls, tot}};
  }}

  const init = buildData("all");
  const chart = new Chart(document.getElementById('countryChart'), {{
    type: 'bar',
    plugins: [ChartDataLabels],
    data: {{
      labels: init.lbs,
      datasets: [{{
        label: 'Лидов',
        data: init.vls,
        backgroundColor: init.lbs.map((_,i) => palette[i % palette.length]),
        borderRadius: 4,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      maintainAspectRatio: false,
      plugins: {{
        legend: {{display: false}},
        tooltip: {{callbacks: {{
          label: function(c) {{
            const tot = c.chart.data.datasets[0].data.reduce((a,b)=>a+b,0);
            return ' ' + c.raw + ' лидов (' + Math.round(c.raw/tot*100) + '%)';
          }}
        }}}},
        datalabels: {{
          anchor: 'end', align: 'end', color: '#e8eaf0',
          font: {{size: 11, weight: 'bold'}},
          formatter: function(val, ctx) {{
            const tot = ctx.chart.data.datasets[0].data.reduce((a,b)=>a+b,0);
            return val + ' (' + Math.round(val/tot*100) + '%)';
          }}
        }}
      }},
      layout: {{padding: {{right: 90}}}},
      scales: {{
        x: {{beginAtZero:true, ticks:{{color:'#e8eaf0'}}, grid:{{color:'#1e2a3a'}}}},
        y: {{ticks:{{color:'#e8eaf0',font:{{size:12}}}}, grid:{{color:'#1e2a3a'}}}}
      }}
    }}
  }});

  const btnWrap = document.getElementById("countrySourceBtns");
  function renderCountryBtns() {{
    btnWrap.innerHTML = "";
    Object.keys(srcData).forEach(k => {{
      const btn = document.createElement("button");
      btn.textContent = srcLabels[k];
      btn.style.cssText = btnStyle(k === activeSrc);
      btn.onclick = function() {{
        activeSrc = k;
        const d = buildData(k);
        chart.data.labels = d.lbs;
        chart.data.datasets[0].data = d.vls;
        chart.data.datasets[0].backgroundColor = d.lbs.map((_,i)=>palette[i%palette.length]);
        chart.update();
        renderCountryBtns();
      }};
      btnWrap.appendChild(btn);
    }});
  }}
  renderCountryBtns();
}})();

// Country status 100% stacked horizontal bar with week filter
(function(){{
  const byWeek  = DATA.cstat_by_week;
  const countries = DATA.country_labels;
  if(!byWeek || !countries.length) return;

  const weekKeys = Object.keys(byWeek);
  const rev = countries.slice().reverse();

  function buildDatasets(ds) {{
    const totals = countries.map((_, ci) => ds.reduce((s, d) => s + (d.data[ci] || 0), 0));
    return ds.map(d => ({{
      label: d.label,
      data: d.data.slice().reverse().map((v, ri) => {{
        const ci = countries.length - 1 - ri;
        return totals[ci] ? Math.round(v / totals[ci] * 100) : 0;
      }}),
      raw: d.data.slice().reverse(),
      backgroundColor: d.color,
      borderRadius: 2,
    }}));
  }}

  const chart = new Chart(document.getElementById('countryStatusChart'), {{
    type: 'bar',
    data: {{ labels: rev, datasets: buildDatasets(byWeek['Все время']) }},
    options: {{
      indexAxis: 'y',
      maintainAspectRatio: false,
      plugins: {{
        legend: {{labels: {{color: '#e8eaf0', boxWidth: 14}}}},
        tooltip: {{
          callbacks: {{
            label: function(c) {{
              const raw = c.dataset.raw[c.dataIndex];
              return ' ' + c.dataset.label + ': ' + raw + ' (' + c.raw + '%)';
            }}
          }}
        }}
      }},
      scales: {{
        x: {{stacked:true, min:0, max:100, ticks:{{color:'#e8eaf0', callback:v=>v+'%'}}, grid:{{color:'#1e2a3a'}}}},
        y: {{stacked:true, ticks:{{color:'#e8eaf0', font:{{size:12}}}}, grid:{{color:'#1e2a3a'}}}}
      }}
    }}
  }});

  // Render week filter buttons
  const btnWrap = document.getElementById('cstatWeekBtns');
  let activeKey = 'Все время';
  const btnStyle = (active) =>
    'padding:5px 12px;border-radius:5px;border:none;cursor:pointer;font-size:12px;font-weight:600;' +
    (active ? 'background:#4f8ef7;color:#fff;' : 'background:#1e2a3a;color:#a0aec0;');

  function renderBtns() {{
    btnWrap.innerHTML = '';
    weekKeys.forEach(k => {{
      const btn = document.createElement('button');
      btn.textContent = k;
      btn.style.cssText = btnStyle(k === activeKey);
      btn.onclick = function() {{
        activeKey = k;
        const newDs = buildDatasets(byWeek[k]);
        chart.data.datasets.forEach((ds, i) => {{
          ds.data = newDs[i].data;
          ds.raw  = newDs[i].raw;
        }});
        chart.update();
        renderBtns();
      }};
      btnWrap.appendChild(btn);
    }});
  }}
  renderBtns();
}})();

// Manager × country heatmap table with week filter
(function(){{
  const cols    = DATA.mgr_country_cols;
  const byWeek  = DATA.mgr_country_by_week;
  const mgrs    = DATA.managers;
  const table   = document.getElementById('mgrCountryTable');
  const btnWrap = document.getElementById('mgrCountryWeekBtns');
  if(!table || !cols || !cols.length || !byWeek) return;

  const weekKeys  = Object.keys(byWeek);
  let activeKey   = 'Все время';

  const btnStyle = (active) =>
    'padding:5px 12px;border-radius:5px;border:none;cursor:pointer;font-size:12px;font-weight:600;' +
    (active ? 'background:#4f8ef7;color:#fff;' : 'background:#1e2a3a;color:#a0aec0;');

  function buildTable(data) {{
    const colMax = cols.map(col =>
      Math.max(1, ...Object.values(data).map(r => r[col] || 0))
    );

    let h = '<thead><tr><th style="text-align:left;padding:6px 10px;color:#a0aec0;border-bottom:1px solid #2a2d3a">Менеджер</th>';
    cols.forEach(col => {{
      h += '<th style="text-align:center;padding:6px 10px;color:#a0aec0;border-bottom:1px solid #2a2d3a">' + col + '</th>';
    }});
    h += '<th style="text-align:center;padding:6px 10px;color:#a0aec0;border-bottom:1px solid #2a2d3a">Итого</th></tr></thead><tbody>';

    const mgrIds = Object.keys(data).filter(id => mgrs[id])
      .sort((a,b) => cols.reduce((s,c) => s + (data[b][c]||0) - (data[a][c]||0), 0));

    mgrIds.forEach(id => {{
      const row   = data[id];
      const total = cols.reduce((s,c) => s + (row[c]||0), 0);
      h += '<tr>';
      h += '<td style="padding:6px 10px;color:#e8eaf0;white-space:nowrap">' + mgrs[id] + '</td>';
      cols.forEach((col, ci) => {{
        const v = row[col] || 0;
        const intensity = colMax[ci] > 0 ? v / colMax[ci] : 0;
        const bg = 'rgba(79,142,247,' + (0.08 + intensity * 0.72).toFixed(2) + ')';
        const textColor = intensity > 0.5 ? '#fff' : '#e8eaf0';
        h += '<td style="text-align:center;padding:6px 10px;background:' + bg + ';color:' + textColor + ';font-weight:' + (v > 0 ? '600' : '400') + '">' + (v || '\u2014') + '</td>';
      }});
      h += '<td style="text-align:center;padding:6px 10px;color:#a0aec0;font-weight:600">' + total + '</td>';
      h += '</tr>';
    }});
    h += '</tbody>';
    table.innerHTML = h;
  }}

  function renderBtns() {{
    btnWrap.innerHTML = '';
    weekKeys.forEach(k => {{
      const btn = document.createElement('button');
      btn.textContent = k;
      btn.style.cssText = btnStyle(k === activeKey);
      btn.onclick = function() {{
        activeKey = k;
        buildTable(byWeek[k]);
        renderBtns();
      }};
      btnWrap.appendChild(btn);
    }});
  }}

  buildTable(byWeek[activeKey]);
  renderBtns();
}})();

// Webinar funnel section
(function(){{
  const d = DATA;
  const funnel = document.getElementById("webinarFunnel");
  if(!funnel) return;

  const preregActive = d.prereg_total - (d.prereg_converted||0) - (d.prereg_disqualified||0) - (d.prereg_real_lost||0);
  const convPct = d.prereg_total ? Math.round((d.prereg_converted||0) / d.prereg_total * 100) : 0;
  const webTotal = (d.web_order_total||0) + (d.web_prepay_total||0);
  const salePct  = webTotal ? Math.round((d.web_total_sales||0) / webTotal * 100) : 0;

  const cards = [
    {{title:"Предзаписей всего",    value: d.prereg_total||0,         color:"#4f8ef7", hint:"Анкета перезаписи 06.2026"}},
    {{title:"Активны сейчас",       value: preregActive > 0 ? preregActive : 0, color:"#00cec9", hint:"Ещё в воронке"}},
    {{title:"Перешли в заказ",      value: d.prereg_converted||0,     color:"#6ab04c", hint:'Причина ЗНР "Оставил Заказ"'}},
    {{title:"Конверсия → заказ",    value: convPct + "%",              color:"#a29bfe", hint:"% предзаписей, оформивших заказ на вебинаре"}},
    {{title:"Заказов с вебинара",   value: d.web_order_total||0,       color:"#f5a623", hint:"Заказ веб 06.26"}},
    {{title:"Предоплат",            value: d.web_prepay_total||0,      color:"#fd79a8", hint:"Предоплата веб 06.26"}},
    {{title:"Продажи с вебинара",   value: d.web_total_sales||0,       color:"#6ab04c", hint:"Статус Продажа из заказов/предоплат"}},
    {{title:"Конверсия → продажа",  value: salePct + "%",              color:"#fdcb6e", hint:"% заказов/предоплат, дошедших до продажи"}},
  ];

  funnel.innerHTML = cards.map(c =>
    '<div class="chart-card" style="padding:16px 20px;min-width:150px">' +
    '<div style="font-size:26px;font-weight:700;color:' + c.color + '">' + c.value + '</div>' +
    '<div style="font-size:13px;color:#e8eaf0;margin-top:4px">' + c.title + '</div>' +
    '<div style="font-size:11px;color:#636e72;margin-top:2px">' + c.hint + '</div>' +
    '</div>'
  ).join('');
}})();

// Closure reasons horizontal bar
new Chart(document.getElementById("reasonChart"),{{
  type:"bar",
  data:{{
    labels:DATA.reason_labels,
    datasets:[{{
      label:"Сделок",
      data:DATA.reason_values,
      backgroundColor:"#eb4d4b",
      borderRadius:4,
    }}]
  }},
  options:{{
    indexAxis:"y",
    maintainAspectRatio:false,
    plugins:{{
      legend:{{display:false}},
      tooltip:{{callbacks:{{
        label:function(c){{
          const total=DATA.reason_values.reduce((a,b)=>a+b,0);
          return ` ${{c.raw}} сделок (${{Math.round(c.raw/total*100)}}%)`;
        }}
      }}}}
    }},
    scales:{{
      x:{{beginAtZero:true,ticks:{{color:"#e8eaf0"}},grid:{{color:"#1e2a3a"}}}},
      y:{{ticks:{{color:"#e8eaf0",font:{{size:12}}}},grid:{{display:false}}}}
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
    price_fmt     = f"{report['total_price']:,}".replace(",", "\u00a0")
    avg_price_fmt = f"{report['avg_price']:,}".replace(",", "\u00a0")

    json_data = json.dumps({
        "sorted_statuses": report["sorted_statuses"],
        "group_counts":    report["group_counts"],
        "managers":        {str(k): v for k, v in report["managers"].items()},
        "mgr_viz":          report["mgr_viz"],
        "mgr_viz_prereg":   report["mgr_viz_prereg"],
        "mgr_viz_web":      report["mgr_viz_web"],
        "overdue":          report["overdue"],
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
        "reason_labels":   report["reason_labels"],
        "reason_values":   report["reason_values"],
        "country_labels":         report["country_labels"],
        "country_values":         report["country_values"],
        "country_labels_prereg":  report["country_labels_prereg"],
        "country_values_prereg":  report["country_values_prereg"],
        "country_labels_web":     report["country_labels_web"],
        "country_values_web":     report["country_values_web"],
        "cstat_datasets":   report["cstat_datasets"],
        "cstat_by_week":    report["cstat_by_week"],
        "mgr_country_cols": report["mgr_country_cols"],
        "mgr_country_data":     report["mgr_country_data"],
        "mgr_country_by_week":  report["mgr_country_by_week"],
        "tariff_labels":    report["tariff_labels"],
        "tariff_values":    report["tariff_values"],
        "revenue_mgr_ids":  report["revenue_mgr_ids"],
        "revenue_values":   report["revenue_values"],
        "mgr_sales_count":  report["mgr_sales_count"],
        "mgr_avg_price":    report["mgr_avg_price"],
        "mgr_conv":         report["mgr_conv"],
        "cumulative_funnel":  report["cumulative_funnel"],
        "cohort_table":       report["cohort_table"],
        "prereg_total":       report["prereg_total"],
        "prereg_converted":   report["prereg_converted"],
        "prereg_disqualified": report["prereg_disqualified"],
        "prereg_real_lost":   report["prereg_real_lost"],
        "web_order_total":    report["web_order_total"],
        "web_prepay_total":   report["web_prepay_total"],
        "web_total_sales":    report["web_total_sales"],
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
        avg_price  = avg_price_fmt,
        json_data  = json_data,
    )

if __name__ == "__main__":
    report = build_report()
    html = generate_html(report)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done. Total: {report['total']}, Sales: {report['group_counts'].get('invoiced',0)+report['group_counts'].get('sale',0)}")
