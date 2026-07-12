# Tasks: Infisical Secret Source Plugin for Hermes Agent

Feature: `0001-base` · Plan: [02-plan.md](02-plan.md) · Status: **draft — awaiting approval**

Conventions: TDD per constitution — for every task the tests are written first,
then the implementation, then run locally until green. A task is done only when
its tests pass and coverage on the touched module stays ≥80%.
`[P]` marks tasks whose prerequisites are met and can run in parallel with
their siblings.

## Phase 0 — scaffolding & risk burn-down

- [ ] #1 → Repo scaffolding: `plugin.yaml` (name/version/description/author),
  `infisical_plugin/` package skeleton (empty modules from plan §4),
  `requirements-dev.txt`, `.gitignore` (`.deps/`, `__pycache__`, `.coverage`)
  (depends: —)
  - Success: `python -c "import infisical_plugin"` works from repo root.
- [ ] #2 → `scripts/fetch-hermes.sh`: clone pinned `hermes-agent` commit into
  `.deps/hermes-agent` (idempotent; pin recorded in the script) (depends: —)
  - Success: script runs twice cleanly; `.deps/hermes-agent/agent/secret_sources/base.py` exists.
- [ ] #3 → `tests/conftest.py`: put `.deps/hermes-agent` on `sys.path`; load
  Hermes' `conformance.py` by file path via importlib (avoids `tests` package
  collision); fail with an actionable message if `.deps` is missing
  (depends: #2)
  - Success: `pytest --collect-only` imports `agent.secret_sources.base` and the conformance module.
- [ ] #4 → Walking skeleton: root `__init__.py` with defensive `register(ctx)`
  registering a stub `InfisicalSource` (returns `NOT_CONFIGURED`); verify a
  real Hermes install loads the plugin from `~/.hermes/plugins/infisical/`
  (plan §7 top import-semantics risk) (depends: #1)
  - Success: `hermes plugins list` (or `HERMES_PLUGINS_DEBUG=1` startup log) shows the plugin loaded, no import errors; loader import semantics documented in a code comment if they differ from plan §4.
- [ ] #5 → CI workflow `.github/workflows/ci.yml`: fetch-hermes, install dev
  deps, run pytest with `--cov=infisical_plugin --cov-fail-under=80`
  (depends: #2, #3)
  - Success: CI green on the branch with the stub-only test suite.

## Phase 1 — pure library (no Hermes imports)

- [P] #6 → `errors.py`: `InfisicalError(kind, message)` + uniform
  `infisical: <kind>: <message>` formatter; tests in `tests/test_errors.py`
  (depends: #3)
  - Success: formatter output shape asserted; kinds round-trip.
- [P] #7 → `refs.py`: `parse_ref()` for the full spec §4 grammar — project /
  environment / optional path / key, `type=dynamic`, `field`, `ttl`, unknown
  query keys rejected; tests in `tests/test_refs.py` (depends: #6)
  - Success: happy paths, `inf://only-a-project` → REF_INVALID, unknown option → REF_INVALID, path normalization (`/` default, nested folders) all covered.
- [P] #8 → `auth.py` — `agent` method: read token from sink file
  (expanduser); missing/empty file → NOT_CONFIGURED naming the checked path;
  tests in `tests/test_auth.py` (depends: #6)
  - Success: tmp-file happy path; missing-file and empty-file error kinds asserted.
- [ ] #9 → `auth.py` — `universal-auth` method: client id/secret from env vars
  or file paths; `POST /api/v1/auth/universal-auth/login` (mocked with
  `responses`); 401/403 → AUTH_FAILED; login called exactly once (depends: #8)
  - Success: env-var and file-path sourcing both tested; token extracted from response; failure kinds asserted.
- [ ] #10 → `auth.py` — `token` method (static `INFISICAL_TOKEN`) + method
  dispatch/default (`agent`) + unknown method → NOT_CONFIGURED (depends: #9)
  - Success: dispatch table covered incl. malformed `auth:` section (non-dict).
- [P] #11 → `client.py` — `Deadline` helper: remaining-budget computation,
  per-call timeout clamping, exhausted → TIMEOUT; tests in
  `tests/test_client.py` (depends: #6)
  - Success: monotonic-clock based; zero/negative remaining handled.
- [ ] #12 → `client.py` — session setup + single HTTP-status→ErrorKind mapping
  (401/403/404/connection/DNS/timeout/other per plan §2.3); TLS verify always
  on (depends: #11)
  - Success: each status class asserted via `responses`; no code path can set `verify=False`.
- [ ] #13 → `client.py` — `get_secret()` → `GET /api/v4/secrets/{key}` with
  projectId/environment/secretPath params; 404 → EMPTY_VALUE (depends: #12)
  - Success: query params asserted; value extracted; trailing data intact.
- [ ] #14 → `client.py` — `list_secrets()` generator with pagination followed
  to completion; O(page) memory (yields pages, never accumulates internally)
  (depends: #12)
  - Success: multi-page mock followed to end; single-page and empty-folder cases; generator laziness asserted.
- [ ] #15 → `client.py` — `resolve_project()`: accept raw ID/slug fast path;
  otherwise projects-listing lookup by ID → slug → display name;
  process-lifetime cache; endpoint shape verified against a live/free
  Infisical instance first (plan §7 risk) (depends: #12)
  - Success: fast path skips HTTP entirely; cache hit skips HTTP; ambiguous/missing name → clear REF_INVALID-adjacent error.
- [ ] #16 → `client.py` — `create_dynamic_lease()` →
  `POST /api/v1/dynamic-secrets/leases` with ttl/path/slugs; response `data`
  payload returned (depends: #12)
  - Success: body fields asserted; lease data round-trip; failure statuses mapped.

## Phase 2 — Hermes adapter layer

- [ ] #17 → `sources.py` — `InfisicalSource` identity + hooks: attributes
  (`name`, `label`, `shape`, `scheme`, `api_version`), `override_existing`
  default True, `protected_env_vars`, full `config_schema()`; tests in
  `tests/test_sources_mapped.py` (depends: #7, #10)
  - Success: all spec §5 keys present in schema; protected vars = {INFISICAL_TOKEN, INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET}.
- [ ] #18 → `sources.py` — mapped `fetch()`: validate env map (invalid
  names/refs → warnings, dropped), empty map → NOT_CONFIGURED, token resolved
  once, refs grouped by project, per-ref failures → warnings (one bad ref
  never sinks the rest), whole-fetch failures → error/error_kind; top-level
  never-raise catch (depends: #13, #15, #17)
  - Success: spec §6 "Mapped secret resolution" + "Authentication" scenarios green against mocked API.
- [ ] #19 → `sources.py` — dynamic refs in mapped `fetch()`: lease creation,
  `field` selection, multi-field payload without `field=` → REF_INVALID
  warning listing available fields (depends: #16, #18)
  - Success: spec §6 "Dynamic secrets" scenarios green.
- [ ] #20 → `sources.py` — `InfisicalBulkSource`: identity
  (`infisical_bulk`, bulk, `override_existing` default False), `fetch()` via
  paginated `list_secrets()`, invalid key names warned and skipped; tests in
  `tests/test_sources_bulk.py` (depends: #14, #17)
  - Success: spec §6 "Bulk injection" scenarios green, incl. multi-page.
- [ ] #21 → Root `__init__.py`: replace stub with real wiring — `register(ctx)`
  registers both sources inside try/except (broken import can never take down
  Hermes startup) (depends: #18, #20)
  - Success: unit test with a fake ctx asserts both registrations and exception swallowing.

## Phase 3 — conformance, hardening, docs

- [ ] #22 → `tests/test_conformance.py`: `SecretSourceConformance` subclasses
  for both sources (depends: #21)
  - Success: Hermes conformance suite passes unmodified for both.
- [P] #23 → Security tests: assert no token, client secret, or secret value
  appears in any `error`/`warning` string across the full failure matrix
  (spec TR-5) (depends: #18, #19, #20)
  - Success: parametrized leak test green; grep-style assertion helper reused across suites.
- [P] #24 → Scalability test: bulk fetch of ~100k mocked secrets completes
  within the 120 s budget with O(page) incremental memory (spec TR-4, plan §8)
  (depends: #20)
  - Success: test bounded in CI time (mock, no sleeps); memory ceiling asserted via tracemalloc.
- [P] #25 → Timeout behavior test: hung API (mocked slow endpoint) → clean
  TIMEOUT FetchResult before the orchestrator budget expires (depends: #18)
  - Success: spec §6 "Timeout budget respected" scenario green.
- [ ] #26 → README.md: install into `~/.hermes/plugins/infisical/`, full config
  reference generated from/checked against `config_schema()`, auth setup for
  all three methods, dynamic-secret TTL limitation, minimum Infisical server
  version, manual end-to-end verification checklist (depends: #21)
  - Success: a doc test asserts README config keys == config_schema() keys (constitution: docs match implementation exactly).
- [ ] #27 → End-to-end verification: load plugin in a real Hermes install
  against a live/free Infisical project; walk the README checklist (mapped +
  bulk + agent-sink auth at minimum) (depends: #22, #26)
  - Success: a real secret lands in the Hermes process env; results recorded in the PR description.
- [ ] #28 → Final pass: coverage report ≥80% every module, `/code-review` of
  the branch, task list updated to `[✓]` with short results (depends: #23,
  #24, #25, #27)
  - Success: CI green; review findings addressed or explicitly deferred with rationale.
