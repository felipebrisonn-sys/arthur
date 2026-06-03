from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v20.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_ACCOUNT_ID = os.getenv("META_ACCOUNT_ID", "act_519770400740924")
CAMPAIGN_NAME_FILTER = os.getenv("CAMPAIGN_NAME_FILTER", "[CAPTADOR]")
PROJECT_LABEL = os.getenv("PROJECT_LABEL", "Funil de Aplicação Captador")
LEAD_ACTION_TYPE = os.getenv("LEAD_ACTION_TYPE", "offsite_conversion.fb_pixel_custom")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1001969196147")
TELEGRAM_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "")
SENT_MARKER_PATH = os.getenv("SENT_MARKER_PATH", "")


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


def graph_url(path: str) -> str:
    version = META_GRAPH_API_VERSION if META_GRAPH_API_VERSION.startswith("v") else f"v{META_GRAPH_API_VERSION}"
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def graph_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_params = {"access_token": META_ACCESS_TOKEN}
    request_params.update(params or {})
    return request_json(graph_url(path), request_params)


def graph_get_all(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = None

    while True:
        request_params = dict(params or {})
        if after:
            request_params["after"] = after

        data = graph_get(path, request_params)
        rows.extend(data.get("data", []))

        paging = data.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            break

    return rows


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


def write_sent_marker(message_ids: list[Any]) -> None:
    if not SENT_MARKER_PATH:
        return

    marker_dir = os.path.dirname(SENT_MARKER_PATH)
    if marker_dir:
        os.makedirs(marker_dir, exist_ok=True)

    with open(SENT_MARKER_PATH, "w", encoding="utf-8") as marker:
        marker.write(f"sent_at={datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()}\n")
        marker.write(f"telegram_message_ids={','.join(str(item) for item in message_ids)}\n")


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value: float) -> str:
    return f"${value:,.2f}"


def percent(value: float) -> str:
    return f"{value:.2f}%"


def whole(value: float) -> str:
    return str(round(value))


def date_br(value) -> str:
    return value.strftime("%d/%m/%Y")


def parse_meta_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def action_total(row: dict[str, Any], action_type: str) -> float:
    return sum(number(item.get("value")) for item in row.get("actions", []) if item.get("action_type") == action_type)


def shorten_adset(name: str) -> str:
    replacements = {
        "03 - Leads Faturam $2,5k - $5k (2025+)": "03 - $2,5k-$5k",
        "03 - Leads Faturam $5k - $10k (2025+)": "03 - $5k-$10k",
        "02 - Leads Faturam $10k+ (2025+)": "02 - $10k+",
        "01 - Alunos Pagos Expirados": "01 - Alunos Expirados",
        "06 - Seguidores e Envolvimento": "06 - Seguidores",
    }
    return replacements.get(name, name)


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    spend = sum(row["spend_n"] for row in rows)
    leads = sum(row["leads_n"] for row in rows)
    page_views = sum(row["page_views_n"] for row in rows)
    link_clicks = sum(row["link_clicks_n"] for row in rows)
    inline_clicks = sum(row["inline_clicks_n"] for row in rows)
    impressions = sum(number(row.get("impressions")) for row in rows)

    return {
        "spend": spend,
        "leads": leads,
        "cpl": spend / leads if leads else 0.0,
        "ctr_link": inline_clicks / impressions * 100 if impressions else 0.0,
        "connect_rate": page_views / link_clicks * 100 if link_clicks else 0.0,
        "page_conversion": leads / page_views * 100 if page_views else 0.0,
    }


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    spend = number(row.get("spend"))
    leads = action_total(row, LEAD_ACTION_TYPE)
    page_views = action_total(row, "landing_page_view")
    link_clicks = action_total(row, "link_click")
    inline_clicks = number(row.get("inline_link_clicks"))
    impressions = number(row.get("impressions"))
    ctr_link = number(row.get("inline_link_click_ctr")) or (inline_clicks / impressions * 100 if impressions else 0.0)

    return {
        **row,
        "spend_n": spend,
        "leads_n": leads,
        "page_views_n": page_views,
        "link_clicks_n": link_clicks,
        "inline_clicks_n": inline_clicks,
        "cpl_n": spend / leads if leads else 0.0,
        "ctr_link_n": ctr_link,
        "connect_rate_n": page_views / link_clicks * 100 if link_clicks else 0.0,
        "page_conversion_n": leads / page_views * 100 if page_views else 0.0,
    }


def metric_line(row: dict[str, Any]) -> str:
    return (
        f"{money(row['spend_n'])} | Leads: {whole(row['leads_n'])} | CPL: {money(row['cpl_n'])}\n"
        f"  CTR link: {percent(row['ctr_link_n'])} | Connect: {percent(row['connect_rate_n'])} "
        f"| Tx pág.: {percent(row['page_conversion_n'])}"
    )


def summary_block(summary: dict[str, float]) -> str:
    return (
        f"  Investido: {money(summary['spend'])}\n"
        f"  Leads: {whole(summary['leads'])}\n"
        f"  CPL: {money(summary['cpl'])}\n"
        f"  CTR link: {percent(summary['ctr_link'])}\n"
        f"  Connect Rate: {percent(summary['connect_rate'])}\n"
        f"  Tx conv. página: {percent(summary['page_conversion'])}"
    )


def chunk_messages(header: str, lines: list[str], max_length: int = 3300) -> list[str]:
    messages: list[str] = []
    current = header

    for line in lines:
        candidate = f"{current}\n{line}"
        if len(candidate) > max_length:
            messages.append(current.strip())
            current = f"{header}\n{line}"
        else:
            current = candidate

    if current.strip():
        messages.append(current.strip())

    return messages


def fetch_account_timezone() -> ZoneInfo:
    account = graph_get(f"/{META_ACCOUNT_ID}", {"fields": "timezone_name"})
    timezone_name = account.get("timezone_name") or "America/New_York"
    return ZoneInfo(timezone_name)


def fetch_active_campaigns() -> list[dict[str, Any]]:
    campaigns = graph_get_all(
        f"/{META_ACCOUNT_ID}/campaigns",
        {"fields": "id,name,status,effective_status,created_time", "limit": "100"},
    )

    filter_text = CAMPAIGN_NAME_FILTER.lower()
    return [
        campaign
        for campaign in campaigns
        if filter_text in campaign.get("name", "").lower()
        and campaign.get("effective_status") == "ACTIVE"
    ]


def fetch_insights(level: str, campaign_ids: list[str], since: str, until: str) -> list[dict[str, Any]]:
    rows = graph_get_all(
        f"/{META_ACCOUNT_ID}/insights",
        {
            "time_range": json.dumps({"since": since, "until": until}),
            "level": level,
            "filtering": json.dumps(
                [{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]
            ),
            "fields": (
                "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
                "spend,impressions,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type"
            ),
            "limit": "500",
            "use_unified_attribution_setting": "true",
        },
    )
    enriched = [enrich_row(row) for row in rows]
    return [
        row
        for row in enriched
        if row["spend_n"] > 0
        or row["leads_n"] > 0
        or row["page_views_n"] > 0
        or row["link_clicks_n"] > 0
        or number(row.get("impressions")) > 0
    ]


def build_messages() -> list[str]:
    account_timezone = fetch_account_timezone()
    today = datetime.now(account_timezone).date()
    period_until = today - timedelta(days=1)
    period_since = period_until - timedelta(days=6)

    campaigns = fetch_active_campaigns()
    if not campaigns:
        print(f"No active campaign containing {CAMPAIGN_NAME_FILTER!r}. Nothing to send.")
        return []

    campaign_ids = [campaign["id"] for campaign in campaigns]
    accumulated_since = min(
        parse_meta_datetime(campaign["created_time"]).date() for campaign in campaigns
    )

    weekly_rows = fetch_insights("campaign", campaign_ids, period_since.isoformat(), period_until.isoformat())
    accumulated_rows = fetch_insights("campaign", campaign_ids, accumulated_since.isoformat(), today.isoformat())
    adset_rows = sorted(
        fetch_insights("adset", campaign_ids, period_since.isoformat(), period_until.isoformat()),
        key=lambda row: row["spend_n"],
        reverse=True,
    )
    ad_rows = sorted(
        fetch_insights("ad", campaign_ids, period_since.isoformat(), period_until.isoformat()),
        key=lambda row: row["spend_n"],
        reverse=True,
    )
    best_ads = sorted(
        [row for row in ad_rows if row["leads_n"] > 0],
        key=lambda row: (-row["leads_n"], row["cpl_n"], -row["connect_rate_n"]),
    )[:8]

    messages = [
        (
            "📊 RELATÓRIO SEMANAL — Arthur Captador\n"
            f"📅 {date_br(period_since)} a {date_br(period_until)} | {PROJECT_LABEL}\n\n"
            "💰 ÚLTIMOS 7 DIAS\n"
            f"{summary_block(summarize(weekly_rows))}\n\n"
            "📈 ACUMULADO GERAL\n"
            f"{summary_block(summarize(accumulated_rows))}"
        )
    ]

    adset_lines = [
        f"{index}. {row.get('adset_name', 'Sem nome')}\n  {metric_line(row)}"
        for index, row in enumerate(adset_rows, start=1)
    ]
    messages.extend(chunk_messages("👥 CONJUNTOS / PÚBLICOS", adset_lines))

    best_ad_lines = [
        f"{index}. {row.get('ad_name', 'Sem nome')} | {shorten_adset(row.get('adset_name', 'Sem conjunto'))}\n"
        f"  {metric_line(row)}"
        for index, row in enumerate(best_ads, start=1)
    ]
    messages.extend(chunk_messages("🏆 MELHORES ANÚNCIOS", best_ad_lines))

    return messages


def main() -> int:
    require_env("META_ACCESS_TOKEN", META_ACCESS_TOKEN)
    require_env("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

    messages = build_messages()
    if not messages:
        return 0

    message_ids = []
    for message in messages:
        result = telegram_send(message)
        if not result.get("ok"):
            raise RuntimeError(f"Telegram send failed: {result}")
        message_ids.append(result.get("result", {}).get("message_id"))

    write_sent_marker(message_ids)
    print(f"Weekly report sent. Telegram message_ids={','.join(str(item) for item in message_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
