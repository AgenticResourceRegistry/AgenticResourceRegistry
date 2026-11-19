---
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Cost Intelligence"
---

Cost Intelligence shows what coding agents cost, where that spend goes (models, harnesses, departments and people) and where it could be lower. It's part of the executive dashboard, so only administrators can see it.

Every figure is calculated from the session usage Agentic Resource Registry already records. A value that can't be calculated is shown as missing, never as zero.

## How cost is calculated

Each model's usage is priced at its provider's published list rate in USD, per token type: input, output, cache reads and cache writes. Spend is the sum over every model a session used, so a session that switches models is priced correctly.

Subscription plans and seat fees are not modeled. Usage on a flat-rate plan, such as Claude Code on a subscription, is shown at the equivalent list price.

The price list covers current Anthropic, OpenAI and Google models. A model it doesn't recognize is listed as **unpriced** with its usage, so you can see what is missing and add a price override.

### What isn't priced

| Harness | What it records | How it's shown |
|---|---|---|
| Kiro | The model and credits, but no token counts | Session and credit counts under **Not priced** |
| Antigravity | No token or model data | Session count under **Not priced** |
| Cursor, OpenCode | Token counts only when their transcripts include usage | Priced when usage is present |

### Savings estimates

The dashboard estimates where spend could be lower, from the selected period scaled to a month. Estimates under $10 a month are hidden.

- **Route short sessions to a cheaper model.** Sessions with fewer than 15 prompts on a model with a cheaper sibling (for example Opus to Sonnet), repriced at the sibling's list rates.
- **Improve prompt caching.** Models with at least $50 of spend whose cache hit rate is 10 or more points below the organization's, repriced as if they matched it.
- **Review high-cost users.** People with at least 20 sessions whose cost per session is 1.5 times their department's median or more. This one has no dollar figure and is not added to the savings total, because it overlaps the other two.

Estimates don't account for quality differences between models. Check before switching.

### Departments

Departments come from SCIM groups, then from each user's department field. People in neither appear under **Unassigned**. Someone in more than one SCIM group is counted in each of their departments, so department totals can add up to more than total spend.

## Configure prices, currency and timezone

An administrator can change these under **Admin > Settings > Cost**. All of them are optional.

| Setting | Default | What it does |
|---|---|---|
| Currency | `USD` | Default display currency, as an ISO code such as `EUR`. Any currency other than USD needs an exchange rate. |
| Exchange Rates | empty | Units of each currency per 1 USD, with the date they were set. Rates are entered by an administrator; no external service is called. |
| Price Overrides | empty | Negotiated or missing model prices in USD per 1M tokens. |
| Timezone | `UTC` | IANA timezone used for day and month boundaries, such as `Europe/London`. |

Exchange rates example:

```json
{"as_of": "2026-11-01", "rates": {"EUR": 0.92, "GBP": 0.79, "INR": 84.10}}
```

Price overrides are keyed by model name, or by a pattern starting with `^` that is matched against the model name a harness reports. An override for a listed model can change only some prices; an override for an unlisted model must give all four.

```json
{
  "claude-opus-5-5": {"input": 3.6, "output": 18},
  "^eu\\.anthropic\\.": {"input": 4.4, "output": 22, "cache_read": 0.22, "cache_write": 5.5}
}
```

Invalid values are rejected when you save them, with a message explaining what to fix.

## API

Both endpoints require an administrator and accept `range=7d`, `30d` (the default) or `90d`. All amounts are in USD; the response includes the configured currency and rates for display. If usage data is temporarily unavailable they return `503` rather than zeros.

| Endpoint | Returns |
|---|---|
| `GET /api/v1/exec/cost/overview` | Headline figures, daily (or, for 90 days, weekly) spend, share of spend by model, spend by harness, and what isn't priced |
| `GET /api/v1/exec/cost/details` | Savings estimates, spend by department and person, and a per-model breakdown by token type, harness and user |

## Known gaps

- Cache writes are priced at the standard cache-write rate, so heavy use of one-hour caching is slightly under-counted.
- Kiro credits are not converted to money.
- Prices change. Keep overrides current for negotiated rates, and update the price list in `observal-server/services/cost/data/model_prices.json` when providers change list prices.
