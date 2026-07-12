# Plan: Infisical Secret Source Plugin for Hermes Agent

Feature: `0001-base` · Spec: [01-spec.md](01-spec.md) · Status: **draft — awaiting approval**

## 0. Findings from the Hermes source (resolves spec open questions)

Verified against [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent)
(`agent/secret_sources/base.py`, `onepassword.py`, `registry.py`,
`hermes_cli/plugins.py`, `tests/secret_sources/conformance.py`):

- **OQ-1 resolved.** `fetch(cfg, home_path)` receives the raw `secrets.<name>`
  config section verbatim — a mapped source reads its own `env:` dict from `cfg`
  and must treat every field defensively (the conformance kit feeds malformed
  configs: `None`, `"env": "not-a-dict"`, bogus TTLs). The conformance kit also
  runs a full `apply_all()` orchestrator round trip.
- The contract is explicitly read-only by design (base.py docstring: "There is
  no write-back… do not bolt it on") — confirms the spec's scope cut.
- `FetchResult` has `secrets`, `warnings`, `error`, `error_kind`, `binary_path`
  (None for API-driven sources like ours), plus legacy `applied`/`skipped` that
  conforming sources leave empty.
- User plugins live at `~/.hermes/plugins/<name>/` with `plugin.yaml`
  (name/version/description/author/kind) **and** `__init__.py` exposing
  `register(ctx)`; `ctx.register_secret_source(...)` may be called more than
  once, so one plugin can register both our sources. User plugins are gated by
  `plugins.enabled` in the user's config.
- Hermes core pins `requests==2.33.0` (and httpx) — `requests` is available to
  in-process plugins at zero extra dependency cost.
- The bundled 1Password source is the reference implementation for a mapped
  source: validate-then-fetch, per-ref failures become warnings (one bad ref
  never sinks the rest), `override_existing` defaults True for mapped,
  best-effort error-text → `ErrorKind` classifier.

## 1. Architecture

```
Hermes startup (orchestrator: agent.secret_sources.registry.apply_all)
    │  fetch(cfg, home_path) per enabled source, timeout-guarded
    ▼
~/.hermes/plugins/infisical/            ← this repository
    __init__.py  register(ctx) ──► InfisicalSource (mapped, scheme "inf")
                              └─► InfisicalBulkSource (bulk)
                                        │ both delegate to
                                        ▼
                          infisical_plugin/  (pure library, no Hermes imports
                                              except agent.secret_sources.base)
        refs.py ──► parse inf:// references
        auth.py ──► resolve access token (agent sink | universal auth | static)
        client.py ─► InfisicalClient: HTTP, retries-free, deadline-aware,
                     project name→ID resolution, pagination, dynamic leases
        errors.py ─► InfisicalError(kind=ErrorKind, message) taxonomy mapping
```

Layering rule: `refs/auth/client/errors` know nothing about `SecretSource`;
`sources.py` is the only adapter layer. This keeps unit tests hermetic and the
two sources thin.

Data flow (mapped): `cfg.env` → validate refs → resolve token once → group refs
by project → resolve project IDs (cached in-process) → per-ref GET (or dynamic
lease POST) → `FetchResult.secrets`. Per-ref failures append to
`result.warnings`; whole-fetch failures (no token, network down) set
`result.error`/`error_kind`.

Data flow (bulk): config names one project/environment/path → paginated LIST →
filter to valid env-var names (invalid keys warned, skipped) → `FetchResult.secrets`.

## 2. Component design

### 2.1 `refs.py`
- `parse_ref(text) -> Ref | raises RefError(kind=REF_INVALID)`.
- `Ref` dataclass: `project`, `environment`, `secret_path` (normalized,
  leading `/`), `key`, `dynamic: bool`, `field: str|None`, `ttl: str|None`.
- Grammar per spec §4: `inf://<project>/<environment>[/<path>…]/<key>[?opts]`.
  Unknown query keys → `REF_INVALID` (fail loud, not silent).

### 2.2 `auth.py`
- `resolve_token(auth_cfg, http) -> Token | raises InfisicalError`.
- Methods, mutually exclusive via `auth.method` (default `agent`):
  - `agent`: read token string from `agent_token_path` sink file (expanduser).
    Missing/empty file → `NOT_CONFIGURED` with the checked path in the message.
  - `universal-auth`: client id/secret from `INFISICAL_CLIENT_ID` /
    `INFISICAL_CLIENT_SECRET` env vars or `client_id_path`/`client_secret_path`
    files; `POST /api/v1/auth/universal-auth/login`; 401/403 → `AUTH_FAILED`.
  - `token`: static token from `INFISICAL_TOKEN`.
- Token is held in memory only; never logged; never written to disk.

### 2.3 `client.py`
- `InfisicalClient(host_url, token, deadline)` wrapping a `requests.Session`
  with `Authorization: Bearer`, TLS verification always on.
- `Deadline` helper: constructed from the source's timeout budget minus a
  safety margin; every HTTP call gets `timeout=min(per_call, remaining)`;
  exhausted deadline → `TIMEOUT` error kind (we return cleanly before the
  orchestrator's hard kill).
- Endpoints:
  - `get_secret(project_id, env, path, key)` → `GET /api/v4/secrets/{key}`.
  - `list_secrets(project_id, env, path, recursive, include_imports)` →
    `GET /api/v4/secrets`, following pagination (`limit`/`offset`) to
    completion; yields secrets in pages (generator) so bulk memory stays
    O(page) beyond the accumulating result map (TR-4).
  - `create_dynamic_lease(project_slug, env, path, name, ttl)` →
    `POST /api/v1/dynamic-secrets/leases`.
  - `resolve_project(name_or_slug_or_id)` → projects listing endpoint, matched
    by ID, slug, then display name; result cached in a module-level dict for
    the process lifetime. References may carry a raw project ID, which skips
    the extra call entirely (mitigation for endpoint drift, see §7).
- HTTP status → `ErrorKind` mapping in one place: 401 `AUTH_FAILED` (or
  `AUTH_EXPIRED` when the body says expired), 403 `AUTH_FAILED`, 404 on a
  secret `EMPTY_VALUE`, connection/DNS errors `NETWORK`, request timeout
  `TIMEOUT`, anything else `INTERNAL`.

### 2.4 `errors.py`
- `InfisicalError(Exception)` carrying `kind: ErrorKind` and a message that is
  safe to surface (references and env-var names only — never values, never
  tokens). Single formatting helper produces the uniform
  `infisical: <kind>: <actionable message>` shape (TR-7).
- Internal layers may raise `InfisicalError`; `sources.py` catches everything
  (`Exception` at the top) and converts to `FetchResult` — the never-raise
  boundary lives in exactly one place.

### 2.5 `sources.py`
- `InfisicalSource(SecretSource)`: `name="infisical"`, `label="Infisical"`,
  `shape="mapped"`, `scheme="inf"`, `api_version` pinned to
  `SECRET_SOURCE_API_VERSION`.
  - `override_existing` defaults **True** (mapped precedent: 1Password).
  - `protected_env_vars` → `{INFISICAL_TOKEN, INFISICAL_CLIENT_ID,
    INFISICAL_CLIENT_SECRET}`.
  - `config_schema()` documents every key from spec §5.
  - `fetch()`: validate `env:` map (invalid names / non-`inf://` refs →
    warnings, dropped); empty valid map → `NOT_CONFIGURED`; resolve token;
    fetch each ref sorted by name; dynamic refs go through the lease path with
    `field` selection (ambiguous multi-field payload without `field=` →
    `REF_INVALID` warning listing available fields).
- `InfisicalBulkSource(SecretSource)`: `name="infisical_bulk"`,
  `label="Infisical (bulk)"`, `shape="bulk"`, no scheme.
  - Note: registry requires `name` to match `[a-z0-9_]+`, so the config key is
    `infisical_bulk` (underscore), not `infisical-bulk` — spec §5 example will
    be corrected when tasks land docs.
  - `override_existing` defaults **False** (bulk yields, per contract).
  - Config inheritance: `host_url`/`auth` fall back to the `infisical`
    section's values when omitted (read via the orchestrator-passed cfg only —
    implemented by having Hermes users duplicate or anchor YAML; at code level
    we accept the same keys in both sections and document YAML anchors as the
    dedup mechanism, since `fetch()` only receives its own section).
- Root `__init__.py`: `register(ctx)` instantiates and registers both sources;
  wrapped in try/except so a broken import can never take down Hermes startup.

### 2.6 Caching
- In-process only for v1: project name→ID map, and the resolved token for the
  duration of one `fetch()`. No disk cache — Hermes' `_cache.py` is private
  API, and secret-value disk caching is a security trade-off we defer
  (recorded in §6).

## 3. Data model

```
Ref            = (project, environment, secret_path, key, dynamic, field, ttl)
AuthConfig     = (method, agent_token_path, client_id, client_secret, …)  # parsed from cfg
Token          = str (opaque bearer token)
Deadline       = (monotonic_start, budget_seconds)
InfisicalError = (kind: ErrorKind, message: str)
FetchResult    = Hermes-owned (secrets, warnings, error, error_kind)
ProjectCache   = {name_or_slug_or_id: project_id}   # module-level, process-lifetime
```

No persistent state of any kind; the plugin owns no files.

## 4. File structure

```
hermes-agent-infisical-plugin/          ← installable as ~/.hermes/plugins/infisical/
├── plugin.yaml                         # name, version, description, author
├── __init__.py                         # register(ctx) — thin, defensive
├── infisical_plugin/
│   ├── __init__.py
│   ├── refs.py
│   ├── auth.py
│   ├── client.py
│   ├── errors.py
│   └── sources.py
├── tests/                              # per constitution: tests beside the tool
│   ├── conftest.py                     # sys.path bootstrap for .deps/hermes-agent
│   ├── test_refs.py
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_errors.py
│   ├── test_sources_mapped.py
│   ├── test_sources_bulk.py
│   ├── test_sources_dynamic.py
│   └── test_conformance.py             # subclasses Hermes' SecretSourceConformance
├── scripts/
│   └── fetch-hermes.sh                 # clone pinned hermes-agent commit into .deps/
├── requirements-dev.txt                # pytest, pytest-cov, responses
├── README.md                           # install + config reference (must match code)
└── .github/workflows/ci.yml            # lint + tests + coverage gate ≥80%
```

Import strategy: the Hermes plugin loader imports the plugin directory as a
module; `__init__.py` adds its own directory to `sys.path` if the relative
package import fails, keeping both loader-import and direct-pytest-import
working. Verified against `hermes_cli/plugins.py` behavior during task #1.

## 5. Dependencies

| Dependency | Kind | Rationale |
|---|---|---|
| `agent.secret_sources.base` | runtime (host-provided) | the contract; only Hermes import |
| `requests` | runtime (host-provided, pinned by Hermes) | HTTP; no new install for users |
| `hermes-agent` checkout (pinned commit) | dev/test only | base classes + conformance kit; fetched into `.deps/` by script, imported via `conftest.py` path setup (loaded by file path to avoid `tests` package-name collision) |
| `pytest`, `pytest-cov` | dev | constitution: TDD, ≥80% coverage |
| `responses` | dev | mock the Infisical HTTP API at the `requests` layer |

No Infisical SDK (TR-8): the four endpoints we need are stable REST, and the
SDK would drag dependencies into every Hermes install.

## 6. Trade-offs

- **REST over SDK** — less surface, zero new runtime deps; cost: we own the
  endpoint shapes (mitigated by contract tests + pinned mock fixtures).
- **No disk cache in v1** — simpler and avoids persisting secret values;
  cost: every Hermes start hits the network. Acceptable: one token call + N
  gets. Revisit if startup latency complaints appear (future feature).
- **Project name resolution as convenience, IDs as fast path** — friendly refs
  per the idea doc; cost: one extra API call, mitigated by accepting raw IDs
  and caching.
- **Bulk source as a separate registered source** — matches the orchestrator's
  mapped-beats-bulk precedence instead of reimplementing precedence ourselves;
  cost: two config sections (YAML anchors dedup the shared auth block).
- **Dynamic leases at startup only** — the contract forbids background
  renewal; documented limitation: lease TTL must cover the session.

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Projects-listing endpoint shape differs from assumption (only endpoint not verified against live docs) | name→ID resolution breaks | refs accept project IDs/slugs directly; resolution isolated in one function; verified first during implementation with a live/free Infisical instance |
| Hermes plugin loader import semantics (package vs module) | plugin fails to load | task #1 is a walking skeleton loaded by a real Hermes install before any feature code |
| `tests` package-name collision with Hermes' own `tests/` | conformance import breaks | load conformance.py by file path via importlib in `conftest.py` |
| API version drift (v4 endpoints are recent) | fetch breaks on older self-hosted Infisical | document minimum server version in README; error messages include HTTP status + endpoint so misversion is diagnosable |
| Timeout budget blown by huge bulk folders | orchestrator kills fetch, no secrets injected | deadline-aware client returns partial-free clean `TIMEOUT` early; pagination page size tuned; `recursive` defaults false |
| Secret leakage via logs/errors | credential exposure | error formatter never interpolates values/tokens; dedicated unit tests assert token and values absent from every error/warning string |

## 8. Success metrics

- Hermes conformance suite passes unmodified.
- A real Hermes install loads the plugin and injects a secret from a live
  Infisical project end-to-end (manual verification checklist in README).
- Coverage ≥80% on every module (constitution); error/edge scenarios from spec
  §6 each map to at least one test.
- Bulk fetch of a 100k-secret folder (mocked) completes within the default
  120 s budget with O(page) incremental memory.
- No secret value or token appears in any log/error output under the full test
  matrix (asserted, not eyeballed).
