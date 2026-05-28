# Automacao gratuita no GitHub Actions

Esta automacao roda na nuvem pelo GitHub Actions, todos os dias as 09h de Brasilia.

O agendamento esta em `.github/workflows/agx-captador-report.yml`:

```yaml
cron: "0 12 * * *"
```

12:00 UTC corresponde a 09:00 em Brasilia.

## Secrets necessarios

No GitHub, abra o repositorio e va em:

`Settings > Secrets and variables > Actions > New repository secret`

Crie:

```txt
META_ACCESS_TOKEN
TELEGRAM_BOT_TOKEN
```

Os demais dados ficam no workflow porque nao sao credenciais sensiveis:

```txt
Conta Meta Ads: act_519770400740924
Filtro: [CAPTADOR]
Telegram chat_id: -1001969196147
Telegram message_thread_id: 5
```

## Regra de envio

O script so envia a mensagem se houver ao menos uma campanha ativa com `[CAPTADOR]` no nome.

Formato enviado:

```txt
📊 BOM DIA — Fechamento de ontem
📅 DD/MM/AAAA | Funil de Aplicação Captador

💰 ONTEM
  Spend: $X,XXX.XX
  Leads: N
  CPL: $X.XX

📈 ACUMULADO GERAL
  Spend: $X,XXX.XX
  Leads: N
  CPL: $X.XX
```
