# Extending checks

There are two extension paths:

1. config-only provider entries in `providers.yaml`;
2. external Python adapters in `~/.config/usagemon/adapters/`.

Prefer config-only when the provider has a simple OpenAI-compatible, `/models`, `/credits`, or balance endpoint. Use Python adapters when parsing auth flows, CLI tools, OAuth files, unusual response shapes, or multi-step APIs.

## Config-only check

```yaml
- id: provider-x
  label: Provider X
  type: openai-compatible
  base_url: https://api.provider-x.com/v1
  models_endpoint: /models
  credential:
    source: keychain
    service: api-usage-monitor/provider-x
    account: default
```

For a credits endpoint:

```yaml
- id: provider-x
  label: Provider X
  type: openai-compatible
  base_url: https://api.provider-x.com
  credits_endpoint: /credits
  currency: USD
  credential:
    source: keychain
    service: api-usage-monitor/provider-x
    account: default
```

## External Python adapter

Create:

```text
~/.config/usagemon/adapters/my_provider.py
```

Minimal adapter:

```python
def check():
    return {
        "id": "my-provider",
        "label": "My Provider",
        "status": "ok",
        "source": "api",
        "balance": {"amount": 10.0, "currency": "USD"},
        "details": ["optional note"],
    }
```

Adapters may return:

- one `dict`;
- one `ProviderStatus`;
- a list mixing dicts and `ProviderStatus` objects.

## Provider status contract

```python
{
    "id": "provider-id",
    "label": "Human label",
    "status": "ok",  # ok | warning | error | rate_limited | quota_exhausted | unknown | unavailable
    "source": "api",
    "balance": {"amount": 12.34, "currency": "USD"},
    "usage": {"amount": 3.21, "currency": "USD"},
    "windows": [
        {
            "label": "Weekly",
            "used_percent": 42,
            "remaining_percent": 58,
            "reset_at": "2026-07-30T00:00:00Z",
            "detail": "optional text",
        }
    ],
    "details": ["optional notes"],
    "message": "short warning/error message",
}
```

## Status semantics

| Status | Meaning |
|---|---|
| `ok` | Healthy/reachable; no action needed |
| `warning` | Usage or balance nearing threshold, or non-fatal endpoint issue |
| `error` | Check failed in a way that needs attention |
| `rate_limited` | Provider reported rate limiting |
| `quota_exhausted` | Credits/window exhausted |
| `unknown` | Provider exists, but monitor cannot determine status yet |
| `unavailable` | Missing CLI, credentials, endpoint, or optional dependency |

## Price tables and cost estimation

Cost estimation lives in:

```text
usage_monitor_app/costs.py
```

It is a pure module: it loads `prices.yaml` (see `docs/CONFIGURATION.md` for the schema) and estimates costs from token counts without any network access. If you build a check that reports token usage, estimate cost with `estimate_cost(table, provider, model, input_tokens=..., output_tokens=..., cached_tokens=...)`; it returns `None` when no price entry exists, so degrade gracefully instead of inventing prices. Never sum `CostEstimate.amount` values across different currencies — use `sum_estimates`, which keeps per-currency totals.

## Safety rules

- Never return tokens, headers, OAuth refresh tokens, or raw credential file contents in `details` or `message`.
- Keep HTTP timeouts short; default is 12 seconds.
- Prefer Keychain over reading another CLI's credential files.
- If a provider endpoint is not stable yet, use `placeholder` or `unknown` instead of guessing.

## Adding a built-in provider type

Built-ins live in:

```text
usage_monitor_app/core.py
```

Steps:

1. add an adapter function returning `ProviderStatus` or `list[ProviderStatus]`;
2. register it in `REGISTRY` (and any aliases, e.g. `xai` / `grok`);
3. add an example in `examples/providers.yaml` if useful;
4. update `docs/CONFIGURATION.md` and this file;
5. add unit tests under `tests/test_adapters.py` with fake HTTP payloads;
6. run smoke tests:

```bash
python3.11 -m compileall usage_monitor_app plugin scripts
python3.11 scripts/usagectl.py status --json --pretty --no-persist
```
