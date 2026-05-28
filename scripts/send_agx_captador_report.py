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
TELEGRAM_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "5")


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


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value: float) -> str:
    return f"${value:,.2f}"


def date_br(value) -> str:
    return value.strftime("%d/%m/%Y")


def parse_meta_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def action_total(row: dict[str, Any], action_type: str) -> float:
    actions = {item.get("action_type"): number(item.get("value")) for item in row.get("actions", [])}
    return actions.get(action_type, 0.0)


def fetch_account_timezone() -> ZoneInfo:
    account = graph_get(f"/{META_ACCOUNT_ID}", {"fields": "timezone_name"})
    timezone_name = account.get("timezone_name") or "America/New_York"
    return ZoneInfo(timezone_name)


def fetch_active_campaigns() -> list[dict[str, Any]]:
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
        and campaign.get("effective_status") == "ACTIVE"
    ]


def fetch_summary(campaign_ids: list[str], since: str, until: str) -> dict[str, float]:
    if not campaign_ids:
        return {"spend": 0.0, "leads": 0.0, "cpl": 0.0}

    data = graph_get(
        f"/{META_ACCOUNT_ID}/insights",
        {
            "time_range": json.dumps({"since": since, "until": until}),
            "level": "campaign",
            "filtering": json.dumps(
                [{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]
            ),
            "fields": "campaign_id,campaign_name,spend,actions,cost_per_action_type",
            "limit": "200",
            "use_unified_attribution_setting": "true",
        },
    )

    rows = data.get("data", [])
    spend = sum(number(row.get("spend")) for row in rows)
    leads = sum(action_total(row, LEAD_ACTION_TYPE) for row in rows)
    cpl = spend / leads if leads else 0.0

    return {"spend": spend, "leads": leads, "cpl": cpl}


def build_message() -> str | None:
    account_timezone = fetch_account_timezone()
    today = datetime.now(account_timezone).date()
    yesterday = today - timedelta(days=1)

    campaigns = fetch_active_campaigns()
    if not campaigns:
        print(f"No active campaign containing {CAMPAIGN_NAME_FILTER!r}. Nothing to send.")
        return None

    campaign_ids = [campaign["id"] for campaign in campaigns]
    accumulated_since = min(
        parse_meta_datetime(campaign["created_time"]).date() for campaign in campaigns
    )

    yesterday_summary = fetch_summary(campaign_ids, yesterday.isoformat(), yesterday.isoformat())
    accumulated_summary = fetch_summary(campaign_ids, accumulated_since.isoformat(), today.isoformat())

    return (
        "📊 BOM DIA — Fechamento de ontem\n"
        f"📅 {date_br(yesterday)} | {PROJECT_LABEL}\n\n"
        "💰 ONTEM\n"
        f"  Spend: {money(yesterday_summary['spend'])}\n"
        f"  Leads: {int(yesterday_summary['leads'])}\n"
        f"  CPL: {money(yesterday_summary['cpl'])}\n\n"
        "📈 ACUMULADO GERAL\n"
        f"  Spend: {money(accumulated_summary['spend'])}\n"
        f"  Leads: {int(accumulated_summary['leads'])}\n"
        f"  CPL: {money(accumulated_summary['cpl'])}"
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

    print(f"Report sent. Telegram message_id={result.get('result', {}).get('message_id')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
