from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v20.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_ACCOUNT_IDS = [
    item.strip()
    for item in os.getenv(
        "META_ACCOUNT_IDS",
        "act_519770400740924,act_8501954776581917",
    ).split(",")
    if item.strip()
]
CAMPAIGN_NAME_FILTER = os.getenv("CAMPAIGN_NAME_FILTER", "[CAPTADOR]")
LEAD_ACTION_TYPE = os.getenv("LEAD_ACTION_TYPE", "offsite_conversion.fb_pixel_custom")

PERIODS = {
    "MAIO": ("2026-05-01", "2026-05-31"),
    "JUNHO": ("2026-06-01", "2026-06-30"),
}


def request_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params or {})}" if params else ""
    request = Request(f"{url}{query}", headers={"Accept": "application/json"})
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
        response = graph_get(path, request_params)
        rows.extend(response.get("data", []))
        paging = response.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            return rows


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def action_total(row: dict[str, Any], action_type: str) -> float:
    return sum(
        number(item.get("value"))
        for item in row.get("actions", [])
        if item.get("action_type") == action_type
    )


def empty_totals() -> dict[str, float]:
    return {
        "spend": 0.0,
        "impressions": 0.0,
        "inline_link_clicks": 0.0,
        "link_clicks": 0.0,
        "page_views": 0.0,
        "leads": 0.0,
    }


def add_totals(target: dict[str, float], source: dict[str, float]) -> None:
    for key in target:
        target[key] += source[key]


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals = empty_totals()
    for row in rows:
        inline_clicks = number(row.get("inline_link_clicks"))
        totals["spend"] += number(row.get("spend"))
        totals["impressions"] += number(row.get("impressions"))
        totals["inline_link_clicks"] += inline_clicks
        totals["link_clicks"] += action_total(row, "link_click") or inline_clicks
        totals["page_views"] += action_total(row, "landing_page_view")
        totals["leads"] += action_total(row, LEAD_ACTION_TYPE)
    return totals


def calculated(totals: dict[str, float]) -> dict[str, float]:
    impressions = totals["impressions"]
    link_clicks = totals["link_clicks"]
    page_views = totals["page_views"]
    return {
        **totals,
        "cpm": totals["spend"] / impressions * 1000 if impressions else 0.0,
        "ctr_link": totals["inline_link_clicks"] / impressions * 100 if impressions else 0.0,
        "connect_rate": page_views / link_clicks * 100 if link_clicks else 0.0,
        "page_conversion": totals["leads"] / page_views * 100 if page_views else 0.0,
    }


def fetch_campaign_ids(account_id: str) -> list[str]:
    campaigns = graph_get_all(
        f"/{account_id}/campaigns",
        {"fields": "id,name,status,effective_status", "limit": "100"},
    )
    filter_text = CAMPAIGN_NAME_FILTER.lower()
    return [
        campaign["id"]
        for campaign in campaigns
        if filter_text in campaign.get("name", "").lower()
    ]


def fetch_period(account_id: str, campaign_ids: list[str], since: str, until: str) -> dict[str, float]:
    if not campaign_ids:
        return empty_totals()
    rows = graph_get_all(
        f"/{account_id}/insights",
        {
            "time_range": json.dumps({"since": since, "until": until}),
            "level": "campaign",
            "filtering": json.dumps(
                [{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]
            ),
            "fields": "campaign_id,campaign_name,spend,impressions,inline_link_clicks,actions",
            "limit": "500",
            "use_unified_attribution_setting": "true",
        },
    )
    return summarize_rows(rows)


def print_summary(label: str, totals: dict[str, float]) -> None:
    data = calculated(totals)
    print(label)
    print(f"Investido: ${data['spend']:.2f}")
    print(f"CPM: ${data['cpm']:.2f}")
    print(f"CTR link: {data['ctr_link']:.2f}%")
    print(f"Impressoes: {round(data['impressions'])}")
    print(f"PageViews: {round(data['page_views'])}")
    print(f"Connect Rate: {data['connect_rate']:.2f}%")
    print(f"Conversao da pagina: {data['page_conversion']:.2f}%")
    print(f"Leads usados na conversao: {round(data['leads'])}")


def main() -> int:
    if not META_ACCESS_TOKEN:
        raise SystemExit("Missing META_ACCESS_TOKEN")

    campaign_ids_by_account = {
        account_id: fetch_campaign_ids(account_id) for account_id in META_ACCOUNT_IDS
    }
    monthly: dict[str, dict[str, float]] = {}
    account_monthly: dict[str, dict[str, dict[str, float]]] = {}

    for label, (since, until) in PERIODS.items():
        monthly[label] = empty_totals()
        for account_id, campaign_ids in campaign_ids_by_account.items():
            totals = fetch_period(account_id, campaign_ids, since, until)
            account_monthly.setdefault(account_id, {})[label] = totals
            add_totals(monthly[label], totals)

    combined = empty_totals()
    for totals in monthly.values():
        add_totals(combined, totals)

    print("CONTAS E CAMPANHAS")
    for account_id, campaign_ids in campaign_ids_by_account.items():
        print(f"- {account_id}: {len(campaign_ids)} campanhas com {CAMPAIGN_NAME_FILTER}")
    print()

    for label in PERIODS:
        print_summary(label, monthly[label])
        print()
    print_summary("MAIO + JUNHO", combined)
    print()

    print("AUDITORIA POR CONTA")
    for account_id in META_ACCOUNT_IDS:
        print(f"- {account_id}")
        for label in PERIODS:
            data = calculated(account_monthly[account_id][label])
            print(
                f"  {label}: Investido ${data['spend']:.2f} | Impressoes {round(data['impressions'])} "
                f"| PageViews {round(data['page_views'])} | Leads {round(data['leads'])}"
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
