from __future__ import annotations

import sys
import os
from datetime import date, datetime

import send_agx_weekly_captador_report as report


def audience_label(campaign_name: str) -> str:
    lowered = campaign_name.lower()
    return "❄️ Frio" if "frio" in lowered or "cold" in lowered else "🔥 Quente"


def build_messages() -> list[str]:
    account_timezone = report.fetch_account_timezone()
    today = datetime.now(account_timezone).date()
    requested_since = os.getenv("REPORT_SINCE", "").strip()
    requested_until = os.getenv("REPORT_UNTIL", "").strip()
    if bool(requested_since) != bool(requested_until):
        raise SystemExit("Preencha REPORT_SINCE e REPORT_UNTIL juntos.")

    campaigns = report.fetch_active_campaigns()
    if not campaigns:
        print(f"No active campaign containing {report.CAMPAIGN_NAME_FILTER!r}. Nothing to send.")
        return []

    campaign_ids = [campaign["id"] for campaign in campaigns]
    if requested_since and requested_until:
        period_since = date.fromisoformat(requested_since)
        period_until = date.fromisoformat(requested_until)
        if period_since > period_until:
            raise SystemExit("REPORT_SINCE nao pode ser posterior a REPORT_UNTIL.")
    else:
        period_since = min(
            report.parse_meta_datetime(campaign["created_time"]).date() for campaign in campaigns
        )
        period_until = today

    campaign_rows = report.fetch_insights(
        "campaign", campaign_ids, period_since.isoformat(), period_until.isoformat()
    )
    adset_rows = sorted(
        report.fetch_insights(
            "adset", campaign_ids, period_since.isoformat(), period_until.isoformat()
        ),
        key=lambda row: row["spend_n"],
        reverse=True,
    )
    ad_rows = report.fetch_insights(
        "ad", campaign_ids, period_since.isoformat(), period_until.isoformat()
    )

    cold_ids = {
        campaign["id"]
        for campaign in campaigns
        if any(keyword in campaign["name"].lower() for keyword in ("frio", "cold"))
    }
    warm_rows = [row for row in campaign_rows if row.get("campaign_id") not in cold_ids]
    cold_rows = [row for row in campaign_rows if row.get("campaign_id") in cold_ids]
    best_ads = sorted(
        [row for row in ad_rows if row["leads_n"] > 0],
        key=lambda row: (-row["leads_n"], row["cpl_n"], -row["connect_rate_n"]),
    )[:10]

    messages = [
        (
            "📊 RELATÓRIO COMPLETO — Arthur Captador | Launch 2\n"
            f"📅 {report.date_br(period_since)} até {report.date_br(period_until)}\n\n"
            "🔥 PÚBLICO QUENTE — ACUMULADO\n"
            f"{report.summary_block(report.summarize(warm_rows))}\n\n"
            "❄️ PÚBLICO FRIO — ACUMULADO\n"
            f"{report.summary_block(report.summarize(cold_rows))}\n\n"
            "📈 ACUMULADO GERAL\n"
            f"{report.summary_block(report.summarize(campaign_rows))}"
        )
    ]

    adset_lines = [
        (
            f"{index}. {audience_label(row.get('campaign_name', ''))} | "
            f"{row.get('adset_name', 'Sem nome')}\n  {report.metric_line(row)}"
        )
        for index, row in enumerate(adset_rows, start=1)
    ]
    messages.extend(report.chunk_messages("👥 CONJUNTOS / PÚBLICOS — ACUMULADO", adset_lines))

    best_ad_lines = [
        (
            f"{index}. {audience_label(row.get('campaign_name', ''))} | "
            f"{row.get('ad_name', 'Sem nome')} | "
            f"{report.shorten_adset(row.get('adset_name', 'Sem conjunto'))}\n"
            f"  {report.metric_line(row)}"
        )
        for index, row in enumerate(best_ads, start=1)
    ]
    messages.extend(report.chunk_messages("🏆 MELHORES ANÚNCIOS — ACUMULADO", best_ad_lines))
    return messages


def main() -> int:
    report.require_env("META_ACCESS_TOKEN", report.META_ACCESS_TOKEN)
    report.require_env("TELEGRAM_BOT_TOKEN", report.TELEGRAM_BOT_TOKEN)

    messages = build_messages()
    message_ids = []
    for message in messages:
        result = report.telegram_send(message)
        if not result.get("ok"):
            raise RuntimeError(f"Telegram send failed: {result}")
        message_ids.append(result.get("result", {}).get("message_id"))

    print(f"Lifetime report sent. Telegram message_ids={','.join(str(item) for item in message_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
