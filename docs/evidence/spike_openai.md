# OpenAI provider probes (2026-11-13)

Raw observations that design §4 relies on. Probes ran against the project key with tiny prompts (total spend under $0.01). Reproduce the model and limit checks with `GET /v1/models` and the `x-ratelimit-*` headers of any Responses call.

## Models visible to the key
GPT-6 line: `gpt-6-astra`, `gpt-6-luna`, `gpt-6-sol`, `gpt-6.1-sol`, `gpt-6.1-terra`. GPT-5 line: `gpt-5.6-{luna,sol,terra}`, `gpt-5.5(-pro)`, `gpt-5.4(-mini,-nano,-pro)`, `gpt-5.2`, `gpt-5.1`, `gpt-5(-mini,-nano,-pro)`. Also `gpt-4.1(-mini)`, `gpt-4o(-mini)`, `o3(-pro)`, `text-embedding-3-small/large`, `omni-moderation-latest`.

## Prices ($/MTok, standard; developers.openai.com/api/docs/pricing)
| Model | Input | Cached input | Output | Batch in/out |
|---|---|---|---|---|
| gpt-6-luna | 0.10 | 0.01 | 0.50 | 0.05 / 0.25 |
| gpt-6.1-terra | 1.00 | 0.05 | 5.00 | 0.50 / 2.50 |
| gpt-6.1-sol | 2.00 | 0.10 | 10.00 | 1.00 / 5.00 |
| gpt-6-astra | 10.00 | 1.00 | 50.00 | 5.00 / 25.00 |
Over 272K input tokens: 2× input and cached rates, 1.5× output for the whole request. Cache writes: 1.25× (30 min) or 2.5× (12 h) on GPT-5.6 and later.

## Strict citation schema (handle enum of 14 values, 3 evidence chunks)
| Model | Effort | Sync total | Segments | Invalid handles |
|---|---|---|---|---|
| gpt-6.1-terra | none | 5.77 s | 2, cited E1.0 / E1.1 | 0 |
| gpt-6-luna | none | 2.31 s | 2, cited E1.0 / E1.1 | 0 |
| gpt-5.4-mini | none | 1.52 s | 3, cited E1.0 / E1.1 / E1.2 | 0 |
| gpt-6.1-terra | low | 3.87 s | 2, cited correctly | 0 |
| gpt-6-luna (unanswerable question) | none | 1.64 s | `abstain`, 0 segments | 0 |
Streaming (`gpt-6.1-terra`, none): first `response.output_text.delta` at 1.41 s, 2.02 s total; events seen: `response.created`, `in_progress`, `output_item.added`, `content_part.added`, `output_text.delta`, `output_text.done`, `content_part.done`, `output_item.done`, `completed`. 319 input tokens, 58 output, 0 reasoning tokens.

## Prompt caching (2,607-token prompt)
| Call | Cached tokens | Cache-write tokens |
|---|---|---|
| gpt-6-luna, first | 0 | 2,604 |
| gpt-6-luna, second | 2,604 | 0 |

## Rate limits from response headers
`gpt-6.1-terra`: 15,000 RPM, 40,000,000 TPM. `gpt-6-luna`: 30,000 RPM, 180,000,000 TPM. Embeddings: 10,000 RPM.

## Flex and effort
- `service_tier=flex`: `gpt-6-luna` served as flex (12.78 s for a trivial call); `gpt-6.1-terra` returned `429 Flex processing is temporarily unavailable`.
- `gpt-6.1-sol` with `reasoning.effort=none` returns 400: supported values are `low`, `medium`, `high`, `xhigh`, `max`.
