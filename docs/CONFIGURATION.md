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

### macOS Keychain — preferred on macOS

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

### Secret file — Linux / systemd

Token in a `0600` file; the recommended source on Linux (macOS: use keychain):

```bash
install -m 600 /dev/null ~/.config/usagemon/secrets/deepseek
echo -n 'TOKEN_HERE' > ~/.config/usagemon/secrets/deepseek
```

```yaml
credential:
  source: file
  path: ~/.config/usagemon/secrets/deepseek
```

With a systemd user service, prefer handing the file via `LoadCredential=`
(or `LoadCredentialEncrypted=` with `systemd-creds`) instead of keeping the
secret in the home directory. systemd exposes it under
`/run/user/<uid>/credentials/<name>`; point `path` at that runtime path:

```ini
[Service]
LoadCredential=deepseek:/etc/creds/deepseek
```

```yaml
credential:
  source: file
  path: /run/user/1000/credentials/deepseek   # your uid
```

Note: `%d` is only expanded inside unit files, not in providers.yaml.

Leading/trailing whitespace is stripped; the file must contain exactly the
token. A missing or unreadable file reports `unavailable` ("No ... credential").

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
  language: en       # en | pt-BR (`pt`, `pt_BR` and `pt-BR.UTF-8` all mean pt-BR)
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
usagemon auth enable --username local
# or
export USAGE_MONITOR_AUTH_PASSWORD='local-only-secret'
```

The login page is an in-dashboard gate; API clients use HTTP Basic Auth.

### Loopback refresh token

Only the password *hash* is stored, so same-machine tools cannot replay a
password they can never read back — the menu bar app included. With auth on,
the backend therefore mints a token at startup into:

```text
~/.config/usagemon/local-token      # 0600, USAGE_MONITOR_LOCAL_TOKEN_FILE overrides
```

A request carrying it as `X-Usage-Monitor-Token` is accepted on `/refresh` and
`/api/v1/refresh`, and nowhere else — it triggers a collection, it does not read
data. Reading the file already implies permission to read `config.yaml` and
restart the server, so it grants nothing new to anyone who has it. Delete the
file to rotate; the next backend start writes a fresh one.

Without it the tray still works: a rejected refresh falls back to collecting
in-process, which is correct but does the backend's work twice.


## Built-in provider types

| Type | Purpose |
|---|---|
| `deepseek` | `/user/balance` check |
| `kimi` | Kimi balance endpoint variants, still endpoint-discovery sensitive |
| `openai-compatible` | Generic OpenAI-compatible reachability/balance endpoint |
| `generic-http` | Alias for generic OpenAI-compatible HTTP check |
| `openai` | OpenAI org costs via Admin key (`/v1/organization/costs`) |
| `supergrok` / `grok` / `grok-subscription` | SuperGrok weekly usage pool via Grok CLI session (`~/.grok/auth.json`) |
| `xai` | xAI Management API prepaid balance (developer API credits) |
| `opencode-go` / `opencode` | OpenCode Go subscription usage windows (`opencode.ai/zen/go/v1/usage`) |
| `charm-hyper` / `hyper` | Charm Hyper credits balance (`hyper.charm.land/v1/credits`) |
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
balance, credits, credit, available, remaining, total_available
```

## OpenAI platform costs (`openai`)

**Important:** remaining prepaid balance is **not** available with a normal
secret/project API key (`sk-...`). The old
`/v1/dashboard/billing/credit_grants` route only accepts a browser **session**
key (`sess-...`) and will return an error for secret keys — that is expected,
not a bug in this monitor.

What works with an official key:

| Goal | Key type | Adapter |
|---|---|---|
| Period spend (last N days) | **Admin** key (`sk-admin-...`) | `type: openai` → `/v1/organization/costs` |
| Remaining credit balance | Browser session only | Not supported (use dashboard) |
| Key still valid / models list | Project secret key | `openai-compatible` + `/v1/models` |

```yaml
- id: openai-api
  label: OpenAI API costs
  type: openai
  limit_days: 30          # 1–180, default 30
  # organization: org-... # optional OpenAI-Organization header
  credential:
    source: keychain
    service: api-usage-monitor/openai
    account: default
```

```bash
# Create an Admin key at:
#   https://platform.openai.com/settings/organization/admin-keys
security add-generic-password -U \
  -a default \
  -s api-usage-monitor/openai \
  -w 'sk-admin-...'
```

Reports `usage` (sum of cost buckets in USD), not `balance`. Details note that
remaining balance is not exposed via Admin/secret keys.

## SuperGrok subscription (`supergrok` / `grok`)

Tracks the **paid SuperGrok / X Premium+ weekly usage pool** (Chat, Build,
Imagine, Voice, …) — the same bar as Settings → Usage on grok.com.

There is no stable public consumer usage API. This adapter reuses the
**unofficial** Grok Build CLI billing surface:

```text
GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
```

Credentials come from the Grok CLI OAuth session (not an API key):

```bash
grok login   # writes ~/.grok/auth.json
```

```yaml
- id: supergrok
  label: SuperGrok
  type: supergrok      # aliases: grok, grok-subscription
  # auth_path: ~/.grok/auth.json
  # warn_percent: 85
```

Behavior:

- Reads `key` + `user_id` from `~/.grok/auth.json` (or `USAGE_MONITOR_GROK_AUTH_FILE`).
- Reports one `windows[]` entry (current week/month) with `used_percent` /
  `remaining_percent` / `reset_at`.
- Product breakdown (`GrokBuild`, `GrokImagine`, …) goes into `details`.
- Extra Usage Credits (top-ups) appear as `balance` when present.
- ≥100% → `quota_exhausted`; ≥ `warn_percent` (default 85) → `warning`.
- Expired / missing session → `unavailable` with a `grok login` hint.

This endpoint can change when the Grok CLI changes; on failure the adapter
degrades to `unavailable` / `warning` rather than inventing numbers.

Also shipped as an external adapter: `plugin/adapters/supergrok.py` (drop into
`~/.config/usagemon/adapters/` for auto-load without a `providers.yaml` entry).

## xAI developer API credits (`xai`)

Prepaid **API** credits (console.x.ai teams), not SuperGrok. xAI does **not**
expose these on the inference API key. Billing lives on the **Management API**
(`https://management-api.x.ai`) and needs a management key from
[console.x.ai → Settings → Management Keys](https://console.x.ai/team/default/management-keys).

```yaml
- id: xai-api
  label: xAI API credits
  type: xai
  # team_id: <uuid>    # optional; auto-discovered from key validation
  # warn_below_usd: 5
  credential:
    source: keychain
    service: api-usage-monitor/xai
    account: default
```

```bash
security add-generic-password -U \
  -a default \
  -s api-usage-monitor/xai \
  -w 'YOUR_XAI_MANAGEMENT_KEY'
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
| `USAGE_MONITOR_SNAPSHOT_MAX_BYTES` | `8388608` (8 MiB) | Rotate the snapshot file to `<name>.1` past this size; `0` disables rotation |
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
| `USAGE_MONITOR_LOCAL_TOKEN_FILE` | `$USAGE_MONITOR_HOME/local-token` | Loopback refresh token (0600, refresh routes only) |
| `USAGE_MONITOR_LANGUAGE` / `USAGE_MONITOR_LANG` | config value | Dashboard/tray language (`en`/`pt-BR`); overrides `config.yaml` |
| `USAGE_MONITOR_AGY_*` | see Antigravity section | Also settable per-entry in `providers.yaml` (`timeout`, `reuse`, `bin`, …) |

## LaunchAgent generation (macOS autostart)

`usagemon autostart` generates LaunchAgent plists for the standalone backend
and menu bar app (see `docs/USAGE.md` for the full flag list). Generation is
file-only: nothing is loaded or enabled, and files are written only to a
directory you pass explicitly.

Defaults derived from the runtime environment:

- Log directory: `$USAGE_MONITOR_HOME/logs` (override with `--log-dir`);
  each agent writes `<name>.out.log` / `<name>.err.log` there. Create the
  directory before bootstrapping an agent — launchd does not create missing
  log directories.
- Python executable: the interpreter running `usagemon` (override with `--python`).
- Working directory / `usagectl.py` path: repo root (override with
  `--working-dir` / `--usagemon`).

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
- Manual use: `usagemon status --notify [--snooze-seconds N]`.
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
