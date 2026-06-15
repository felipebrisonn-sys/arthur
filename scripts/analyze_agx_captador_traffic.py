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
LEAD_ACTION_TYPE = os.getenv("LEAD_ACTION_TYPE", "offsite_conversion.fb_pixel_custom")


def request_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    encoded = urlencode(params or {}).encode("utf-8")
    query = f"?{encoded.decode('utf-8')}" if encoded else ""
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

        data = graph_get(path, request_params)
        rows.extend(data.get("data", []))

        paging = data.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            break

    return rows


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def action_total(row: dict[str, Any], action_type: str) -> float:
    return sum(number(item.get("value")) for item in row.get("actions", []) if item.get("action_type") == action_type)


def money(value: float) -> str:
    return f"${value:,.2f}"


def pct(value: float) -> str:
    return f"{value:.2f}%"


def whole(value: float) -> str:
    return str(round(value))


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100 if denominator else 0.0


def enrich(row: dict[str, Any]) -> dict[str, Any]:
    spend = number(row.get("spend"))
    impressions = number(row.get("impressions"))
    reach = number(row.get("reach"))
    frequency = number(row.get("frequency"))
    inline_clicks = number(row.get("inline_link_clicks"))
    landing_page_views = action_total(row, "landing_page_view")
    link_clicks = action_total(row, "link_click") or inline_clicks
    leads = action_total(row, LEAD_ACTION_TYPE)

    return {
        **row,
        "spend_n": spend,
        "impressions_n": impressions,
        "reach_n": reach,
        "frequency_n": frequency,
        "link_clicks_n": link_clicks,
        "landing_page_views_n": landing_page_views,
        "leads_n": leads,
        "ctr_link_n": number(row.get("inline_link_click_ctr")) or ratio(inline_clicks, impressions),
        "cpc_link_n": spend / link_clicks if link_clicks else 0.0,
        "cpm_n": spend / impressions * 1000 if impressions else 0.0,
        "connect_rate_n": ratio(landing_page_views, link_clicks),
        "page_conversion_n": ratio(leads, landing_page_views),
        "cpl_n": spend / leads if leads else 0.0,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    spend = sum(row["spend_n"] for row in rows)
    impressions = sum(row["impressions_n"] for row in rows)
    reach = sum(row["reach_n"] for row in rows)
    link_clicks = sum(row["link_clicks_n"] for row in rows)
    landing_page_views = sum(row["landing_page_views_n"] for row in rows)
    leads = sum(row["leads_n"] for row in rows)

    return {
        "spend": spend,
        "impressions": impressions,
        "reach": reach,
        "frequency": impressions / reach if reach else 0.0,
        "link_clicks": link_clicks,
        "landing_page_views": landing_page_views,
        "leads": leads,
        "ctr_link": ratio(link_clicks, impressions),
        "cpc_link": spend / link_clicks if link_clicks else 0.0,
        "cpm": spend / impressions * 1000 if impressions else 0.0,
        "connect_rate": ratio(landing_page_views, link_clicks),
        "page_conversion": ratio(leads, landing_page_views),
        "cpl": spend / leads if leads else 0.0,
    }


def fetch_account_timezone() -> ZoneInfo:
    account = graph_get(f"/{META_ACCOUNT_ID}", {"fields": "timezone_name"})
    return ZoneInfo(account.get("timezone_name") or "America/New_York")


def fetch_matching_campaigns() -> list[dict[str, Any]]:
    campaigns = graph_get_all(
        f"/{META_ACCOUNT_ID}/campaigns",
        {"fields": "id,name,status,effective_status,created_time", "limit": "100"},
    )
    filter_text = CAMPAIGN_NAME_FILTER.lower()
    return [
        campaign
        for campaign in campaigns
        if filter_text in campaign.get("name", "").lower()
        and campaign.get("effective_status") in {"ACTIVE", "PAUSED"}
    ]


def fetch_insights(level: str, campaign_ids: list[str], since: str, until: str, time_increment: str | None = None) -> list[dict[str, Any]]:
    fields = (
        "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
        "spend,impressions,reach,frequency,inline_link_clicks,inline_link_click_ctr,actions"
    )
    if level == "ad":
        fields += ",quality_ranking,engagement_rate_ranking,conversion_rate_ranking"

    params: dict[str, Any] = {
        "time_range": json.dumps({"since": since, "until": until}),
        "level": level,
        "filtering": json.dumps([{"field": "campaign.id", "operator": "IN", "value": campaign_ids}]),
        "fields": fields,
        "limit": "500",
        "use_unified_attribution_setting": "true",
    }
    if time_increment:
        params["time_increment"] = time_increment

    return [enrich(row) for row in graph_get_all(f"/{META_ACCOUNT_ID}/insights", params)]


def line_summary(title: str, data: dict[str, float]) -> str:
    return (
        f"{title}\n"
        f"Spend: {money(data['spend'])} | Leads: {whole(data['leads'])} | CPL: {money(data['cpl'])}\n"
        f"CTR link: {pct(data['ctr_link'])} | CPC link: {money(data['cpc_link'])} | CPM: {money(data['cpm'])}\n"
        f"Connect Rate: {pct(data['connect_rate'])} | Tx pagina: {pct(data['page_conversion'])} | Freq: {data['frequency']:.2f}"
    )


def table_row(row: dict[str, Any], name_key: str) -> str:
    return (
        f"- {row.get(name_key, 'Sem nome')}\n"
        f"  Spend {money(row['spend_n'])} | Leads {whole(row['leads_n'])} | CPL {money(row['cpl_n'])} | "
        f"CTR {pct(row['ctr_link_n'])} | CPC {money(row['cpc_link_n'])} | "
        f"Connect {pct(row['connect_rate_n'])} | Tx pag {pct(row['page_conversion_n'])} | Freq {row['frequency_n']:.2f}"
    )


def add_findings(summary: dict[str, float], adsets: list[dict[str, Any]], ads: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []

    if summary["ctr_link"] < 1.5:
        findings.append("CTR de link baixo no agregado: precisa testar criativos/ganchos mais fortes e mais qualificadores.")
    elif summary["ctr_link"] < 3:
        findings.append("CTR de link ok, mas sem folga: ainda existe espaco para melhorar criativo e promessa.")
    else:
        findings.append("CTR de link bom: o problema nao parece ser somente atracao do clique.")

    if summary["connect_rate"] < 70:
        findings.append("Connect Rate baixo: ha perda relevante entre clique e carregamento da pagina. Revisar velocidade, redirecionamento e tracking.")
    elif summary["connect_rate"] < 85:
        findings.append("Connect Rate aceitavel, mas melhoravel: pequenos ganhos de pagina podem baratear o lead.")
    else:
        findings.append("Connect Rate bom: pouca evidencia de problema tecnico entre clique e pagina.")

    if summary["page_conversion"] >= 20:
        findings.append("Taxa da pagina alta: se nao fecha venda, o formulario provavelmente esta permissivo demais ou vendendo expectativa errada.")
    elif summary["page_conversion"] >= 10:
        findings.append("Taxa da pagina mediana: da para testar formulario mais qualificador sem destruir volume.")
    else:
        findings.append("Taxa da pagina baixa: alem da qualidade, pode haver problema de oferta/pagina.")

    spend_by_adset = sorted(adsets, key=lambda row: row["spend_n"], reverse=True)
    if spend_by_adset:
        worst_spend = [row for row in spend_by_adset if row["spend_n"] >= summary["spend"] * 0.12 and row["leads_n"] == 0]
        for row in worst_spend[:3]:
            findings.append(f"Conjunto com gasto relevante sem lead: {row.get('adset_name', 'Sem nome')} ({money(row['spend_n'])}).")

        best = [row for row in spend_by_adset if row["leads_n"] >= 3]
        if best:
            best_sorted = sorted(best, key=lambda row: row["cpl_n"])
            findings.append(f"Melhor eficiencia em conjunto: {best_sorted[0].get('adset_name', 'Sem nome')} com CPL {money(best_sorted[0]['cpl_n'])}.")

    poor_ads = [
        row for row in ads
        if row["spend_n"] >= max(20.0, summary["spend"] * 0.05)
        and row["leads_n"] == 0
    ]
    if poor_ads:
        findings.append(f"Ha anuncios com gasto relevante sem lead; pausar ou trocar criativo dos piores antes de escalar.")

    return findings


def main() -> int:
    if not META_ACCESS_TOKEN:
        raise SystemExit("Missing META_ACCESS_TOKEN")

    account_timezone = fetch_account_timezone()
    today = datetime.now(account_timezone).date()
    until = today - timedelta(days=1)
    since = until - timedelta(days=6)

    campaigns = fetch_matching_campaigns()
    if not campaigns:
        print(f"Nenhuma campanha ativa com {CAMPAIGN_NAME_FILTER!r}.")
        return 0

    campaign_ids = [campaign["id"] for campaign in campaigns]
    campaign_rows = fetch_insights("campaign", campaign_ids, since.isoformat(), until.isoformat())
    adset_rows = sorted(fetch_insights("adset", campaign_ids, since.isoformat(), until.isoformat()), key=lambda row: row["spend_n"], reverse=True)
    ad_rows = sorted(fetch_insights("ad", campaign_ids, since.isoformat(), until.isoformat()), key=lambda row: row["spend_n"], reverse=True)
    daily_rows = fetch_insights("campaign", campaign_ids, since.isoformat(), until.isoformat(), "1")
    summary = summarize(campaign_rows)

    print("ANALISE TRAFEGO - AGX CAPTADOR")
    print(f"Periodo: {since.strftime('%d/%m/%Y')} a {until.strftime('%d/%m/%Y')}")
    print("Campanhas consideradas:")
    for campaign in campaigns:
        print(f"- {campaign['name']} ({campaign['id']}) | {campaign.get('effective_status')}")
    print()
    print(line_summary("RESUMO GERAL", summary))
    print()

    print("EVOLUCAO DIARIA")
    for row in sorted(daily_rows, key=lambda item: item.get("date_start", "")):
        print(
            f"- {row.get('date_start')} | {row.get('campaign_name', 'Sem campanha')}: "
            f"Spend {money(row['spend_n'])} | Leads {whole(row['leads_n'])} | "
            f"CPL {money(row['cpl_n'])} | CTR {pct(row['ctr_link_n'])} | Connect {pct(row['connect_rate_n'])} | "
            f"Tx pag {pct(row['page_conversion_n'])}"
        )
    print()

    print("CONJUNTOS - ORDENADO POR GASTO")
    for row in adset_rows:
        print(table_row(row, "adset_name"))
    print()

    print("ANUNCIOS - TOP POR GASTO")
    for row in ad_rows[:12]:
        print(table_row(row, "ad_name"))
        print(f"  Conjunto: {row.get('adset_name', 'Sem conjunto')}")
        rankings = [
            row.get("quality_ranking"),
            row.get("engagement_rate_ranking"),
            row.get("conversion_rate_ranking"),
        ]
        if any(rankings):
            print(f"  Rankings: qualidade={rankings[0]} | engajamento={rankings[1]} | conversao={rankings[2]}")
    print()

    print("ACHADOS AUTOMATICOS")
    for finding in add_findings(summary, adset_rows, ad_rows):
        print(f"- {finding}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
