from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v20.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_ACCOUNT_ID = os.getenv("META_ACCOUNT_ID", "act_8501954776581917")
CAMPAIGN_NAME_FILTER = os.getenv("CAMPAIGN_NAME_FILTER", "[CAPTADOR]")
PROJECT_LABEL = os.getenv("PROJECT_LABEL", "Funil de Aplicação Captador")
LEAD_ACTION_TYPE = os.getenv("LEAD_ACTION_TYPE", "offsite_conversion.fb_pixel_custom")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1001969196147")
TELEGRAM_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "5")
SENT_MARKER_PATH = os.getenv("SENT_MARKER_PATH", "")

TYPEFORM_ACCESS_TOKEN = os.getenv("TYPEFORM_ACCESS_TOKEN", "").strip()
TYPEFORM_FORM_ID = os.getenv("TYPEFORM_FORM_ID", "").strip()
TYPEFORM_FORM_TITLE = os.getenv("TYPEFORM_FORM_TITLE", "Captador de C.A.S.A.").strip()
TYPEFORM_API_BASE = "https://api.typeform.com"
BR_TZ = ZoneInfo("America/Sao_Paulo")
UTC_TZ = ZoneInfo("UTC")


def require_env(name: str, value: str) -> str:
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def request_json(url: str, params: dict[str, Any] | None = None, method: str = "GET") -> dict[str, Any]:
    encoded = urlencode(params or {}).encode("utf-8")
    if method == "GET":
        query = f"?{encoded.decode('utf-8')}" if encoded else ""
        request = Request(f"{url}{query}", headers={"Accept": "application/json"})
    else:
        request = Request(
            url,
            data=encoded,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method=method,
        )

    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Connection error: {error}") from error


def request_json_with_headers(
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query = f"?{urlencode(params or {})}" if params else ""
    request = Request(f"{url}{query}", headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Connection error: {error}") from error


def graph_url(path: str) -> str:
    version = META_GRAPH_API_VERSION if META_GRAPH_API_VERSION.startswith("v") else f"v{META_GRAPH_API_VERSION}"
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def graph_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_params = {"access_token": META_ACCESS_TOKEN}
    request_params.update(params or {})
    return request_json(graph_url(path), request_params)


def typeform_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return request_json_with_headers(
        f"{TYPEFORM_API_BASE}/{path.lstrip('/')}",
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {TYPEFORM_ACCESS_TOKEN}",
        },
        params,
    )


def telegram_send(text: str) -> dict[str, Any]:
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    if TELEGRAM_MESSAGE_THREAD_ID:
        params["message_thread_id"] = TELEGRAM_MESSAGE_THREAD_ID

    return request_json(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        params,
        method="POST",
    )


def write_sent_marker(message_id: Any) -> None:
    if not SENT_MARKER_PATH:
        return

    marker_dir = os.path.dirname(SENT_MARKER_PATH)
    if marker_dir:
        os.makedirs(marker_dir, exist_ok=True)

    with open(SENT_MARKER_PATH, "w", encoding="utf-8") as marker:
        marker.write(f"sent_at={datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()}\n")
        marker.write(f"telegram_message_id={message_id}\n")


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value: float) -> str:
    return f"${value:,.2f}"


def percent(value: float) -> str:
    return f"{value:.2f}%"


def quality_block(quality: dict[str, Any] | None) -> str:
    if not quality or quality.get("unavailable"):
        return "  Typeform: sem dados disponíveis"

    total = int(quality["total"])
    if not total:
        return "  Typeform: 0 leads pagos"

    lines = [
        "  Taxa de Aprovação (Typeform)",
        f"  Leads pagos Typeform: {total}",
        f"  Aprovados: {int(quality['approved'])} | {percent(quality['approved_pct'])}",
        f"  Nutrição: {int(quality['nurture'])} | {percent(quality['nurture_pct'])}",
        f"  Desqualificados: {int(quality['disqualified'])} | {percent(quality['disqualified_pct'])}",
    ]
    if quality.get("unknown"):
        lines.append(f"  Sem tag: {int(quality['unknown'])} | {percent(quality['unknown_pct'])}")
    return "\n".join(lines)


def summary_block(summary: dict[str, float], quality: dict[str, Any] | None = None) -> str:
    meta_lines = (
        f"  Spend: {money(summary['spend'])}\n"
        f"  Leads: {int(summary['leads'])}\n"
        f"  CPL: {money(summary['cpl'])}\n"
        f"  CPM: {money(summary['cpm'])}\n"
        f"  CTR link: {percent(summary['ctr_link'])}\n"
        f"  Connect Rate: {percent(summary['connect_rate'])}\n"
        f"  Conversão de Página: {percent(summary['page_conversion'])}"
    )
    return f"{meta_lines}\n{quality_block(quality)}"


def date_br(value) -> str:
    return value.strftime("%d/%m/%Y")


def parse_meta_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def action_total(row: dict[str, Any], action_type: str) -> float:
    actions = {item.get("action_type"): number(item.get("value")) for item in row.get("actions", [])}
    return actions.get(action_type, 0.0)


def ratio(part: float, total: float) -> float:
    return part / total * 100 if total else 0.0


def fetch_account_timezone() -> ZoneInfo:
    account = graph_get(f"/{META_ACCOUNT_ID}", {"fields": "timezone_name"})
    timezone_name = account.get("timezone_name") or "America/New_York"
    return ZoneInfo(timezone_name)


def fetch_campaigns() -> list[dict[str, Any]]:
    campaigns: list[dict[str, Any]] = []
    after = None

    while True:
        params: dict[str, Any] = {
            "fields": "id,name,status,effective_status,created_time",
            "limit": "100",
        }
        if after:
            params["after"] = after

        data = graph_get(f"/{META_ACCOUNT_ID}/campaigns", params)
        campaigns.extend(data.get("data", []))

        paging = data.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            break

    filter_text = CAMPAIGN_NAME_FILTER.lower()
    return [
        campaign
        for campaign in campaigns
        if filter_text in campaign.get("name", "").lower()
    ]


def fetch_summary(campaign_ids: list[str], since: str, until: str) -> dict[str, float]:
    if not campaign_ids:
        return empty_meta_summary()

    data = graph_get(
        f"/{META_ACCOUNT_ID}/insights",
        {
            "time_range": json.dumps({"since": since, "until": until}),
            "level": "campaign",
            "filtering": json.dumps(
                [{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]
            ),
            "fields": "campaign_id,campaign_name,spend,impressions,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type",
            "limit": "200",
            "use_unified_attribution_setting": "true",
        },
    )

    rows = data.get("data", [])
    spend = sum(number(row.get("spend")) for row in rows)
    impressions = sum(number(row.get("impressions")) for row in rows)
    link_clicks = sum(number(row.get("inline_link_clicks")) for row in rows)
    page_views = sum(action_total(row, "landing_page_view") for row in rows)
    leads = sum(action_total(row, LEAD_ACTION_TYPE) for row in rows)
    cpl = spend / leads if leads else 0.0
    cpm = spend / impressions * 1000 if impressions else 0.0

    return {
        "spend": spend,
        "impressions": impressions,
        "link_clicks": link_clicks,
        "page_views": page_views,
        "leads": leads,
        "cpl": cpl,
        "cpm": cpm,
        "ctr_link": ratio(link_clicks, impressions),
        "connect_rate": ratio(page_views, link_clicks),
        "page_conversion": ratio(leads, page_views),
    }


def empty_meta_summary() -> dict[str, float]:
    return {
        "spend": 0.0,
        "impressions": 0.0,
        "link_clicks": 0.0,
        "page_views": 0.0,
        "leads": 0.0,
        "cpl": 0.0,
        "cpm": 0.0,
        "ctr_link": 0.0,
        "connect_rate": 0.0,
        "page_conversion": 0.0,
    }


def split_campaigns(campaigns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cold_campaigns = [
        campaign
        for campaign in campaigns
        if any(keyword in campaign.get("name", "").lower() for keyword in ("frio", "cold"))
    ]
    cold_ids = {campaign["id"] for campaign in cold_campaigns}
    warm_campaigns = [campaign for campaign in campaigns if campaign["id"] not in cold_ids]
    return warm_campaigns, cold_campaigns


def accumulated_summary(campaigns: list[dict[str, Any]], since_date, until_date) -> dict[str, float]:
    if not campaigns:
        return empty_meta_summary()

    campaign_ids = [campaign["id"] for campaign in campaigns]
    return fetch_summary(campaign_ids, since_date.isoformat(), until_date.isoformat())


def normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.upper()


def br_date_range_to_utc(start_date: date, end_date: date) -> tuple[str, str]:
    start_dt = datetime.combine(start_date, time.min, tzinfo=BR_TZ).astimezone(UTC_TZ)
    end_dt = datetime.combine(end_date, time.max, tzinfo=BR_TZ).astimezone(UTC_TZ)
    return (
        start_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        end_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )


def resolve_typeform_form_id() -> str | None:
    if TYPEFORM_FORM_ID:
        return TYPEFORM_FORM_ID
    if not TYPEFORM_ACCESS_TOKEN or not TYPEFORM_FORM_TITLE:
        return None

    expected = normalize_text(TYPEFORM_FORM_TITLE)
    page = 1
    while True:
        response = typeform_get("/forms", {"page": page, "page_size": 200})
        forms = response.get("items", [])
        for form in forms:
            if normalize_text(form.get("title")) == expected:
                return str(form.get("id"))

        page_count = int(response.get("page_count") or page)
        if page >= page_count or not forms:
            return None
        page += 1


def get_typeform_responses(form_id: str, since: str, until: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = None
    while True:
        params: dict[str, Any] = {
            "page_size": 1000,
            "response_type": "completed",
            "sort": "submitted_at,asc",
            "since": since,
            "until": until,
        }
        if after:
            params["after"] = after

        response = typeform_get(f"/forms/{form_id}/responses", params)
        items = response.get("items", [])
        rows.extend(items)
        if not items or len(items) < 1000:
            return rows
        after = items[-1].get("token") or items[-1].get("response_id")
        if not after:
            return rows


def hidden_for(row: dict[str, Any]) -> dict[str, Any]:
    hidden = row.get("hidden") or {}
    return hidden if isinstance(hidden, dict) else {}


def typeform_value_from_variable(variable: dict[str, Any]) -> Any:
    for key in ("text", "number", "boolean"):
        if key in variable:
            return variable[key]
    return None


def typeform_utm_value(row: dict[str, Any], *names: str) -> str:
    hidden = hidden_for(row)
    for name in names:
        value = hidden.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def is_paid_typeform_lead(row: dict[str, Any]) -> bool:
    medium = typeform_utm_value(row, "utm_medium", "medium", "h_utm_medium")
    return "pago" in medium.lower()


def typeform_scope(row: dict[str, Any]) -> str:
    hidden = hidden_for(row)
    utm_text = " ".join(str(value) for value in hidden.values() if value not in (None, ""))
    normalized = normalize_text(utm_text)
    if "FRIO" in normalized or "COLD" in normalized:
        return "cold"
    return "warm"


def scoring_text(row: dict[str, Any]) -> str:
    preferred_keys = {
        "prd_resultado_lead_scoring",
        "resultado_lead_scoring",
        "lead_scoring",
        "lead_score",
        "qualificacao",
        "qualification",
        "tag",
        "tags",
    }

    candidates: list[str] = []
    for variable in row.get("variables") or []:
        key = str(variable.get("key") or "")
        value = typeform_value_from_variable(variable)
        if value in (None, ""):
            continue
        value_text = str(value)
        if key in preferred_keys:
            return value_text
        candidates.append(value_text)

    hidden = hidden_for(row)
    for key, value in hidden.items():
        if value in (None, ""):
            continue
        if str(key) in preferred_keys:
            return str(value)
        candidates.append(str(value))

    for value in candidates:
        normalized = normalize_text(value)
        if any(token in normalized for token in ("Z10K", "MQL", "MQLS", "LEADS", "DESQUALIFIC")):
            return value
    return ""


def quality_category(row: dict[str, Any]) -> str:
    text = normalize_text(scoring_text(row))
    tokens = set(re.split(r"[^A-Z0-9]+", text))
    if "DESQUALIFIC" in text:
        return "disqualified"
    if "Z10K" in tokens or "MQL" in tokens or "MQLS" in tokens:
        return "approved"
    if "LEADS" in tokens or "LEAD" in tokens:
        return "nurture"
    return "unknown"


def typeform_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    counts = {
        "approved": 0,
        "nurture": 0,
        "disqualified": 0,
        "unknown": 0,
    }
    for row in rows:
        counts[quality_category(row)] += 1

    return {
        "total": total,
        **counts,
        "approval_rate": ratio(counts["approved"], total),
        "approved_pct": ratio(counts["approved"], total),
        "nurture_pct": ratio(counts["nurture"], total),
        "disqualified_pct": ratio(counts["disqualified"], total),
        "unknown_pct": ratio(counts["unknown"], total),
    }


def unavailable_typeform_summary() -> dict[str, Any]:
    return {"unavailable": True}


def fetch_typeform_quality(start_date: date, end_date: date) -> dict[str, dict[str, Any]]:
    unavailable = {
        "warm": unavailable_typeform_summary(),
        "cold": unavailable_typeform_summary(),
        "total": unavailable_typeform_summary(),
    }
    if not TYPEFORM_ACCESS_TOKEN:
        return unavailable

    form_id = resolve_typeform_form_id()
    if not form_id:
        return unavailable

    since, until = br_date_range_to_utc(start_date, end_date)
    paid_rows = [
        row
        for row in get_typeform_responses(form_id, since, until)
        if is_paid_typeform_lead(row)
    ]
    warm_rows = [row for row in paid_rows if typeform_scope(row) == "warm"]
    cold_rows = [row for row in paid_rows if typeform_scope(row) == "cold"]

    return {
        "warm": typeform_quality_summary(warm_rows),
        "cold": typeform_quality_summary(cold_rows),
        "total": typeform_quality_summary(paid_rows),
    }


def build_message() -> str | None:
    account_timezone = fetch_account_timezone()
    today = datetime.now(account_timezone).date()
    yesterday = today - timedelta(days=1)
    month_start = yesterday.replace(day=1)

    campaigns = fetch_campaigns()
    active_campaigns = [
        campaign
        for campaign in campaigns
        if campaign.get("effective_status") == "ACTIVE"
    ]
    if not active_campaigns:
        print(f"No active campaign containing {CAMPAIGN_NAME_FILTER!r}. Nothing to send.")
        return None

    warm_campaigns, cold_campaigns = split_campaigns(campaigns)
    warm_ids = [campaign["id"] for campaign in warm_campaigns]
    cold_ids = [campaign["id"] for campaign in cold_campaigns]
    all_ids = [campaign["id"] for campaign in campaigns]

    warm_yesterday = fetch_summary(warm_ids, yesterday.isoformat(), yesterday.isoformat())
    cold_yesterday = fetch_summary(cold_ids, yesterday.isoformat(), yesterday.isoformat())
    total_yesterday = fetch_summary(all_ids, yesterday.isoformat(), yesterday.isoformat())

    warm_accumulated = accumulated_summary(warm_campaigns, month_start, yesterday)
    cold_accumulated = accumulated_summary(cold_campaigns, month_start, yesterday)
    total_accumulated = accumulated_summary(campaigns, month_start, yesterday)

    try:
        quality_yesterday = fetch_typeform_quality(yesterday, yesterday)
        quality_accumulated = fetch_typeform_quality(month_start, yesterday)
    except Exception as error:
        print(f"Typeform quality unavailable: {error}", file=sys.stderr)
        quality_yesterday = {
            "warm": unavailable_typeform_summary(),
            "cold": unavailable_typeform_summary(),
            "total": unavailable_typeform_summary(),
        }
        quality_accumulated = quality_yesterday

    print("Publico quente:", ", ".join(campaign["name"] for campaign in warm_campaigns) or "nenhuma")
    print("Publico frio:", ", ".join(campaign["name"] for campaign in cold_campaigns) or "nenhuma")
    print("Campanhas ativas:", ", ".join(campaign["name"] for campaign in active_campaigns) or "nenhuma")

    return (
        "📊 BOM DIA — Fechamento de ontem\n"
        f"📅 {date_br(yesterday)} | {PROJECT_LABEL}\n\n"
        "🔥 PÚBLICO QUENTE — ONTEM\n"
        f"{summary_block(warm_yesterday, quality_yesterday['warm'])}\n\n"
        "❄️ PÚBLICO FRIO — ONTEM\n"
        f"{summary_block(cold_yesterday, quality_yesterday['cold'])}\n\n"
        "💰 TOTAL ONTEM\n"
        f"{summary_block(total_yesterday, quality_yesterday['total'])}\n\n"
        f"📈 ACUMULADO GERAL — PÚBLICO QUENTE ({date_br(month_start)} até {date_br(yesterday)})\n"
        f"{summary_block(warm_accumulated, quality_accumulated['warm'])}\n\n"
        f"📈 ACUMULADO GERAL — PÚBLICO FRIO ({date_br(month_start)} até {date_br(yesterday)})\n"
        f"{summary_block(cold_accumulated, quality_accumulated['cold'])}\n\n"
        f"📈 ACUMULADO GERAL — MÊS ATUAL ({date_br(month_start)} até {date_br(yesterday)})\n"
        f"{summary_block(total_accumulated, quality_accumulated['total'])}"
    )


def main() -> int:
    require_env("META_ACCESS_TOKEN", META_ACCESS_TOKEN)
    require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

    message = build_message()
    if not message:
        return 0

    result = telegram_send(message)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {result}")

    message_id = result.get("result", {}).get("message_id")
    write_sent_marker(message_id)
    print(f"Report sent. Telegram message_id={message_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
