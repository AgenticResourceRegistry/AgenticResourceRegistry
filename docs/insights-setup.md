---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Insights LLM Setup"
---

The insights engine uses [LiteLLM](https://docs.litellm.ai/docs/providers) to call LLMs for report generation. This means any LiteLLM-compatible provider works out of the box.

## Quick Start

1. Go to **Admin → Settings → Agent Insights**
2. Set **API Key** (your provider's key)
3. Set **Sections Model** in `provider/model-name` format
4. Set **Synthesis Model** and **Facets Model** for cost optimization

That's it. Reports will generate on the next batch run.

## Settings Reference

| Setting | Purpose | Required |
|---------|---------|----------|
| API Key | Provider authentication token | Yes |
| API Base URL | Custom endpoint override | Only for Azure, Bedrock, Ollama |
| Sections Model | Writes detailed narrative sections | Yes |
| Synthesis Model | Aggregation and "At a Glance" | No (falls back to Sections) |
| Facets Model | Per-session structured extraction | No (falls back to Sections) |

## Model Strategy

Three model slots let you optimize cost vs quality:

- **Sections Model**: use your best model (Opus, GPT-4o, Gemini Pro). This writes the detailed analysis. Runs once per report.
- **Synthesis Model**: mid-tier (Sonnet, GPT-4o). Does cross-session aggregation. Runs a few times per report. Falls back to Sections Model if unset.
- **Facets Model**: cheapest/fastest (Haiku, GPT-4o-mini, Flash). Runs once per session in the report (potentially hundreds of calls). This is where cost optimization matters most.

## Provider Examples

### Anthropic (direct API)

| Setting | Value |
|---------|-------|
| API Key | `sk-ant-api03-...` |
| API Base URL | _(leave blank)_ |
| Sections Model | `anthropic/claude-sonnet-4-20250514` |
| Synthesis Model | `anthropic/claude-sonnet-4-20250514` |
| Facets Model | `anthropic/claude-haiku-4-5-20251001` |

### OpenAI

| Setting | Value |
|---------|-------|
| API Key | `sk-proj-...` |
| API Base URL | _(leave blank)_ |
| Sections Model | `openai/gpt-4o` |
| Synthesis Model | `openai/gpt-4o` |
| Facets Model | `openai/gpt-4o-mini` |

### AWS Bedrock

Generate a Bedrock API key from the [AWS Console](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) (Bedrock → API keys). Short-term keys last up to 12 hours; long-term keys last until expiry.

| Setting | Value |
|---------|-------|
| API Key | `<your-bedrock-bearer-token>` |
| API Base URL | `https://bedrock-runtime.us-east-1.amazonaws.com` |
| Sections Model | `bedrock/us.anthropic.claude-opus-4-6-v1` |
| Synthesis Model | `bedrock/us.anthropic.claude-sonnet-4-6-v1` |
| Facets Model | `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` |

Replace `us-east-1` in the base URL with your region.

### Google Gemini (AI Studio)

Get an API key from [Google AI Studio](https://aistudio.google.com/apikey).

| Setting | Value |
|---------|-------|
| API Key | `AIza...` |
| API Base URL | _(leave blank)_ |
| Sections Model | `gemini/gemini-2.5-pro` |
| Synthesis Model | `gemini/gemini-2.5-pro` |
| Facets Model | `gemini/gemini-2.5-flash` |

### Azure OpenAI

| Setting | Value |
|---------|-------|
| API Key | Your Azure API key |
| API Base URL | `https://<instance>.openai.azure.com` |
| Sections Model | `azure/<deployment-name>` |
| Synthesis Model | `azure/<deployment-name>` |
| Facets Model | `azure/<deployment-name-mini>` |

### Ollama (local)

| Setting | Value |
|---------|-------|
| API Key | _(leave blank)_ |
| API Base URL | `http://localhost:11434` |
| Sections Model | `ollama/llama3` |
| Synthesis Model | `ollama/llama3` |
| Facets Model | `ollama/llama3` |

### Other providers

Any LiteLLM-supported provider works. See the full list: https://docs.litellm.ai/docs/providers

Common examples:
- Mistral: `mistral/mistral-large-latest`
- Groq: `groq/llama-3.3-70b-versatile`
- Together AI: `together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo`
- Deepseek: `deepseek/deepseek-chat`

## Batch Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Batch Processing | `true` | Enable/disable automatic report generation |
| Batch Period | `14` days | How often to check for new reports |
| Minimum Sessions | `5` | Sessions needed before generating a report |
| Max Facet Calls | `100` | LLM call limit for facet extraction per report |
| Facet Concurrency | `25` | Parallel facet extraction calls |

## Troubleshooting

**"litellm: call failed"** in logs: check your API key, model ID format, and that the provider is reachable from the server.

**Empty reports**: ensure at least one model is configured (Sections Model at minimum) and the API key has the correct permissions.

**High costs**: reduce Facet Concurrency and Max Facet Calls, or use a cheaper Facets Model. The Facets Model runs once per session, so this is where most LLM spend happens.

**Bedrock auth failures**: verify the API key hasn't expired (short-term keys last 12 hours max). Use long-term keys for production.
