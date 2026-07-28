# Contributing

Thanks for helping improve `usage-monitor`.

The project is designed to run as a standalone Python app. Hermes integration is optional, so contributors do **not** need Hermes installed to develop or test the core package.

## Local setup

```bash
git clone <repo-url> usage-monitor
cd usage-monitor
make setup
```

`make setup` creates `.venv` and installs the package with the `dev` and `menubar` extras.

If you do not use `make`:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[dev,menubar]'
```

## Run checks

```bash
make test
```

The test target runs:

- `pytest`
- `compileall` over package, tests, scripts, and plugin files
- shell syntax checks for `install.sh` and `uninstall.sh`
- `git diff --check`

For a narrower loop while editing:

```bash
.venv/bin/python -m pytest tests/test_web.py -q
```

## Run the local app

```bash
make server
# open http://127.0.0.1:9097
```

Useful API smoke checks:

```bash
curl -s http://127.0.0.1:9097/api/v1/status | python -m json.tool
curl -sX POST http://127.0.0.1:9097/api/v1/refresh > /dev/null
```

The local server binds to `127.0.0.1` by default and has no auth. Do not expose it to the network.

## Configuration for development

Default state lives in `~/.config/usagemon/`. You can isolate development state with env vars:

```bash
export USAGE_MONITOR_HOME="$PWD/.tmp/usage"
export USAGE_MONITOR_PROVIDERS_FILE="$PWD/examples/providers.yaml"
```

Do not commit real provider configs, tokens, snapshots, or local state.

## Adding a provider

Prefer config-only support first:

- `openai-compatible` / `generic-http` for `/models` or balance-style endpoints
- `deepseek`, `kimi`, and other built-ins when a stable endpoint exists
- `placeholder` when a provider is tracked but has no public usage endpoint yet

If config cannot express the provider, add an external adapter-compatible `check()` implementation. The return value may be a dict, a `ProviderStatus`, or a list of either:

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

Rules:

- never return API keys, tokens, raw headers, or full credential objects;
- use timeouts for network calls;
- degrade to `unavailable` or `unknown` with a short message instead of raising through the whole collection;
- add tests with mocked network/CLI boundaries.

## Code style

- Keep imports lazy for optional integrations such as Hermes and macOS-only tooling.
- Core collection should stay safe in standalone environments.
- Preserve backwards-compatible routes when adding API endpoints.
- Keep the dashboard as static, dependency-free HTML/CSS/JS unless the project explicitly adopts a frontend build step.
- Prefer small focused patches over broad refactors.

## Pull request checklist

Before opening a PR:

- [ ] `make test` passes.
- [ ] New provider behavior has tests or documented manual verification.
- [ ] Docs/examples are updated when config, routes, or CLI flags change.
- [ ] No secrets, local configs, snapshots, or generated LaunchAgent files are committed.
- [ ] Standalone mode works without Hermes unless the change is explicitly Hermes-only.
