# Configuration

Main files:

```text
~/.config/usagemon/providers.yaml  # provider registry
~/.config/usagemon/config.yaml     # dashboard/auth/intervals
```

Sample:

```text
examples/providers.yaml
```

Examples live in the repo:

```text
examples/providers.yaml
examples/config.yaml
examples/prices.yaml
```

Copy them manually; `make install-tray` does not install Hermes or overwrite real config.

## Minimal provider registry

```yaml
defaults:
  timeout: 12

providers:
  - id: deepseek
    label: DeepSeek
    type: deepseek
    credential:
      source: keychain
      service: api-usage-monitor/deepseek
      account: default
```

## Credential sources

### macOS Keychain — preferred

Store a token:

```bash
security add-generic-password \
  -a default \
  -s api-usage-monitor/deepseek \
  -w 'TOKEN_HERE'
```

Config:

```yaml
credential:
  source: keychain
  service: api-usage-monitor/deepseek
  account: default
```

### Environment variable

```bash
export DEEPSEEK_API_KEY='TOKEN_HERE'
```

```yaml
credential:
  source: env
  name: DEEPSEEK_API_KEY
```

### Hermes credential pool

Use only when running in an environment that has Hermes installed/configured:

```yaml
credential:
  source: hermes
  provider: deepseek
```

The core package does not import Hermes on startup. Hermes modules are imported lazily only by Hermes-specific adapters.

### Literal value — avoid

Supported for local tests only:

```yaml
credential:
  source: literal
  value: TOKEN_HERE
```

Do not commit real tokens.

## App behavior (`config.yaml`)

```yaml
dashboard:
  show_auth: false
  language: en       # en | pt
  theme: light
intervals:
  refresh_seconds: 900   # backend scheduler; 0 disables
  menubar_seconds: 300   # tray refresh; 0 disables
auth:
  enabled: false
  realm: usage-monitor
  username: local
  # password_hash: pbkdf2_sha256$390000$<salt-hex>$<hash-hex>
```

Enable Basic Auth without writing plaintext to config:

```bash
usagectl auth enable --username local
# or
export USAGE_MONITOR_AUTH_PASSWORD='local-only-secret'
```

The login page is an in-dashboard gate; API clients use HTTP Basic Auth.


## Built-in provider types

| Type | Purpose |
|---|---|
| `deepseek` | `/user/balance` check |
| `kimi` | Kimi balance endpoint variants, still endpoint-discovery sensitive |
| `openai-compatible` | Generic OpenAI-compatible reachability/balance endpoint |
| `generic-http` | Alias for generic OpenAI-compatible HTTP check |
| `placeholder` | Lists a provider without a stable usage endpoint yet |
| `hermes-account-usage` | Optional Hermes `agent.account_usage` bridge |
| `anthropic-subscription` | Optional Hermes Anthropic account-usage bridge |
| `hermes-auth` | `hermes auth list` parser |
| `hermes-nous` | Optional Nous portal credit lines via Hermes |
| `hermes-state-db` | Read-only aggregation of recorded usage from Hermes `state.db` |

## Hermes state.db (read-only)

The `hermes-state-db` adapter aggregates recorded usage from one or more
Hermes `state.db` SQLite files. It opens each database with a read-only URI
(`mode=ro`), never writes, never takes a write lock, and needs no
credentials or network access.

```yaml
- id: hermes-state
  label: Hermes state.db
  type: hermes-state-db
  db_paths:                  # optional; defaults to $HERMES_HOME/state.db
    - ~/.hermes/state.db
  profile: default           # optional; only sessions of this profile
  limit_days: 30             # optional; only rows seen in the last N days
```

Behavior:

- Returns one `ProviderStatus` per `billing_provider`, aggregating
  `session_model_usage` rows by provider/model (calls, tokens, cost).
- `usage` is reported in USD from `actual_cost_usd` when present, otherwise
  `estimated_cost_usd`.
- When a provider/model group has no recorded cost, the adapter optionally
  estimates one from `prices.yaml` (via `usage_monitor_app/costs.py`) and
  reports it in `details` under its own currency — estimates are never mixed
  into the USD `usage` figure.
- Missing/unopenable database → `unavailable`; missing tables/columns
  (schema drift) → `unknown`; no matching rows → `ok` with an empty-usage
  message.

## OpenAI-compatible checks

Reachability via `/models`:

```yaml
- id: local-mlx
  label: Local oMLX
  type: openai-compatible
  base_url: http://127.0.0.1:8000/v1
  models_endpoint: /models
  credential:
    source: env
    name: LOCAL_MLX_API_KEY
```

Credits endpoint:

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

The generic parser looks for these numeric fields:

```text
balance, credits, credit, available, remaining
```

## Runtime env vars

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Hermes-compatible base dir |
| `USAGE_MONITOR_HOME` | `~/.config/usagemon` | App config/state dir |
| `USAGE_MONITOR_PROVIDERS_FILE` | `$USAGE_MONITOR_HOME/providers.yaml` | Provider registry |
| `USAGE_MONITOR_CONFIG_FILE` | `$USAGE_MONITOR_HOME/config.yaml` | Dashboard/auth/interval config |
| `USAGE_MONITOR_PRICES_FILE` | `$USAGE_MONITOR_HOME/prices.yaml` | External price table |
| `USAGE_MONITOR_ADAPTER_DIR` | `$USAGE_MONITOR_HOME/adapters` | External Python adapters |
| `USAGE_MONITOR_SNAPSHOT_FILE` | `$USAGE_MONITOR_HOME/snapshots.jsonl` | Snapshot JSONL file |
| `USAGE_MONITOR_OVERRIDES_FILE` | `$USAGE_MONITOR_HOME/overrides.json` | Per-provider overrides (relevance mute) |
| `USAGE_MONITOR_REFRESH_INTERVAL` | `config.yaml intervals.refresh_seconds` | Standalone scheduler interval in seconds |
| `USAGE_MONITOR_ALERT_STATE_FILE` | `$USAGE_MONITOR_HOME/alert_state.json` | Alert dedup/snooze state file |
| `USAGE_MONITOR_ALERT_SNOOZE_SECONDS` | `3600` | Snooze window before an alert re-notifies |
| `USAGE_MONITOR_ALERT_NOTIFY` | `1` | Set to `0` to track dedup state without calling osascript |
| `USAGE_MONITOR_MENUBAR_INTERVAL` | `300` | Menu bar app auto-refresh interval in seconds (`0` disables) |
| `USAGE_MONITOR_DASHBOARD_URL` | `http://127.0.0.1:9097` | URL opened by the menu bar app's `Open Dashboard` action |
| `USAGE_MONITOR_AUTH_ENABLED` | config value | Force Basic Auth on/off |
| `USAGE_MONITOR_AUTH_USERNAME` | config value | Basic Auth username |
| `USAGE_MONITOR_AUTH_PASSWORD` | unset | Basic Auth plaintext from env |
| `USAGE_MONITOR_AUTH_PASSWORD_HASH` | unset | Basic Auth PBKDF2 hash from env |
| `USAGE_MONITOR_LANGUAGE` / `USAGE_MONITOR_LANG` | config value | Dashboard/tray language (`en`/`pt`) |
| `GCP_ACCESS_TOKEN` | unset | OAuth token for `gcp_billing.py` adapter |
| `GCP_PROJECT_ID` | unset | GCP project ID for `gcp_billing.py` adapter |
| `GCP_BILLING_ACCOUNT_ID` | unset | GCP billing account ID for `gcp_billing.py` adapter |
| `USAGE_MONITOR_GCP_BUDGET_USD` | unset | Target monthly GCP budget for `gcp_billing.py` adapter |
| `USAGE_MONITOR_AGY_BIN` | PATH lookup | Path to the `agy` binary for `antigravity.py` adapter |
| `USAGE_MONITOR_AGY_TIMEOUT` | `30` | Seconds to wait for `agy` quota data |
| `USAGE_MONITOR_AGY_SETTLE` | `1.0` | Extra settle seconds before the final quota read |
| `USAGE_MONITOR_AGY_CACHE_TTL` | `300` | Cache lifetime in seconds (`0` disables) |
| `USAGE_MONITOR_AGY_CACHE` | `~/.config/usagemon/cache/antigravity.json` | Cache file path |
| `USAGE_MONITOR_AGY_REUSE` | unset | `1` reads a running `agy` instead of spawning one |
| `USAGE_MONITOR_AGY_WARN_PCT` | `15` | Warn below this remaining percent |

## LaunchAgent generation (macOS autostart)

`usagectl autostart` generates LaunchAgent plists for the standalone backend
and menu bar app (see `docs/USAGE.md` for the full flag list). Generation is
file-only: nothing is loaded or enabled, and files are written only to a
directory you pass explicitly.

Defaults derived from the runtime environment:

- Log directory: `$USAGE_MONITOR_HOME/logs` (override with `--log-dir`);
  each agent writes `<name>.out.log` / `<name>.err.log` there. Create the
  directory before bootstrapping an agent — launchd does not create missing
  log directories.
- Python executable: the interpreter running `usagectl` (override with `--python`).
- Working directory / `usagectl.py` path: repo root (override with
  `--working-dir` / `--usagectl`).

Credentials stay where they already are (Keychain/env/`providers.yaml`); the
generated plists contain no secrets, only paths and numeric flags.

## Alerts (dedup + macOS notifications)

`usage_monitor_app/alerts.py` turns snapshot alerts into local macOS
notifications with dedup/snooze. It is wired into the standalone scheduler
only: after each `collect_status(persist=True)` tick, new alerts (not seen
before, or past their snooze window) are recorded in `alert_state.json` and
sent via `osascript display notification`. Core `collect_status` stays pure
and never notifies.

- Dedup key: `level|provider|message`.
- State is recorded before notifying, so a broken notifier cannot cause
  alert spam; notification failures are tolerated silently.
- Manual use: `usagectl status --notify [--snooze-seconds N]`.
- Tests inject a fake command runner; nothing notifies for real.

## Price table

Price table path:

```text
~/.config/usagemon/prices.yaml
```

Sample:

```text
examples/prices.yaml
```

### Schema (version 1)

```yaml
version: 1

providers:
  deepseek:
    currency: CNY                                          # one currency per provider
    source: https://api-docs.deepseek.com/quick_start/pricing  # optional provenance
    models:
      deepseek-chat:
        input: 2.0     # price per 1M input tokens
        output: 8.0    # price per 1M output tokens
        cached: 0.5    # optional, per 1M cached input tokens
```

Rules:

- All prices are per 1M tokens, in the provider's single declared `currency` (defaults to `USD`).
- Model names are normalized on lookup: lowercase, whitespace stripped, leading `models/` removed, `:tag` suffixes (e.g. `:free`) and trailing date stamps (`-2024-08-06`, `-20240806`) removed.
- Cached tokens are billed at the `cached` price when present, otherwise at the `input` price.
- A provider entry without `models` (legacy currency/source-only form) loads fine but yields no estimates.

### Cost estimation API

`usage_monitor_app/costs.py` is a pure module (no network, no credentials):

```python
from usage_monitor_app.costs import load_price_table, estimate_cost, sum_estimates

table = load_price_table()  # reads USAGE_MONITOR_PRICES_FILE; corrupt/missing -> empty table
est = estimate_cost(table, "deepseek", "deepseek-chat",
                    input_tokens=12000, output_tokens=3000, cached_tokens=50000)
# est -> CostEstimate(amount=..., currency="CNY", input_cost=..., output_cost=..., cached_cost=...)
# est is None when provider/model/prices are missing.
totals = sum_estimates([est, ...])  # {"CNY": ...}; never sums across currencies
```

Current status: cost estimation is available as a library but is not yet wired into snapshots or the dashboard.
