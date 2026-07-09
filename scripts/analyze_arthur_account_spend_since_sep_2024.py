from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v20.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
ACCOUNT_NAME_FILTER = os.getenv("ACCOUNT_NAME_FILTER", "Arthur").strip()
SINCE = os.getenv("SINCE", "2024-09-01")
UNTIL = os.getenv("UNTIL", date.today().isoformat())
KNOWN_ACCOUNT_IDS = [
    item.strip()
    for item in os.getenv(
        "KNOWN_ACCOUNT_IDS",
        "act_519770400740924,act_8501954776581917",
    ).split(",")
    if item.strip()
]


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


def normalize_account_id(account_id: str) -> str:
    account_id = account_id.strip()
    return account_id if account_id.startswith("act_") else f"act_{account_id}"


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_ranges(since: str, until: str) -> list[tuple[str, str]]:
    start = parse_date(since)
    end = parse_date(until)
    ranges: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = min(date(cursor.year, cursor.month, last_day), end)
        ranges.append((cursor.isoformat(), month_end.isoformat()))
        cursor = month_end + timedelta(days=1)
    return ranges


def fetch_available_accounts() -> list[dict[str, Any]]:
    return graph_get_all(
        "/me/adaccounts",
        {
            "fields": "id,name,account_status,currency,timezone_name",
            "limit": "100",
        },
    )


def fetch_account(account_id: str) -> dict[str, Any]:
    return graph_get(
        f"/{normalize_account_id(account_id)}",
        {"fields": "id,name,account_status,currency,timezone_name"},
    )


def select_accounts() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    available = fetch_available_accounts()
    filter_text = ACCOUNT_NAME_FILTER.lower()

    for account in available:
        account_id = normalize_account_id(str(account.get("id", "")))
        name = str(account.get("name", ""))
        if filter_text and filter_text in name.lower():
            by_id[account_id] = {**account, "id": account_id}

    for account_id in KNOWN_ACCOUNT_IDS:
        normalized_id = normalize_account_id(account_id)
        if normalized_id in by_id:
            continue
        by_id[normalized_id] = fetch_account(normalized_id)
        by_id[normalized_id]["id"] = normalized_id

    return sorted(by_id.values(), key=lambda row: str(row.get("name", "")).lower())


def fetch_spend_for_range(account_id: str, since: str, until: str) -> float:
    response = graph_get(
        f"/{normalize_account_id(account_id)}/insights",
        {
            "level": "account",
            "time_range": json.dumps({"since": since, "until": until}),
            "fields": "spend",
            "limit": "10",
        },
    )
    return sum(number(row.get("spend")) for row in response.get("data", []))


def fetch_total_spend(account_id: str) -> tuple[float, list[tuple[str, str, float]]]:
    monthly: list[tuple[str, str, float]] = []
    total = 0.0
    for since, until in month_ranges(SINCE, UNTIL):
        spend = fetch_spend_for_range(account_id, since, until)
        monthly.append((since, until, spend))
        total += spend
    return total, monthly


def money(value: float, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def main() -> int:
    if not META_ACCESS_TOKEN:
        raise SystemExit("Missing META_ACCESS_TOKEN")

    accounts = select_accounts()
    if not accounts:
        print(f"Nenhuma conta encontrada com filtro: {ACCOUNT_NAME_FILTER}")
        return 0

    results: list[dict[str, Any]] = []
    for account in accounts:
        account_id = normalize_account_id(str(account["id"]))
        total, monthly = fetch_total_spend(account_id)
        results.append({**account, "id": account_id, "total": total, "monthly": monthly})

    print("RELATORIO INVESTIMENTO ARTHUR")
    print(f"Periodo: {SINCE} ate {UNTIL}")
    print(f"Filtro de nome: {ACCOUNT_NAME_FILTER or '(sem filtro)'}")
    print()

    grand_totals: dict[str, float] = {}
    for row in sorted(results, key=lambda item: number(item["total"]), reverse=True):
        currency = str(row.get("currency") or "USD")
        grand_totals[currency] = grand_totals.get(currency, 0.0) + number(row["total"])
        print(f"Conta: {row.get('name', row['id'])}")
        print(f"ID: {row['id']}")
        print(f"Moeda: {currency}")
        print(f"Investimento total: {money(number(row['total']), currency)}")
        print()

    print("TOTAL GERAL")
    for currency, total in sorted(grand_totals.items()):
        print(f"{currency}: {money(total, currency)}")

    print()
    print("AUDITORIA MENSAL")
    for row in sorted(results, key=lambda item: str(item.get("name", "")).lower()):
        currency = str(row.get("currency") or "USD")
        print(f"- {row.get('name', row['id'])} ({row['id']})")
        for since, until, spend in row["monthly"]:
            if spend:
                print(f"  {since} a {until}: {money(spend, currency)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
