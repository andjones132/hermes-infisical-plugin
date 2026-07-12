# Tasks: Infisical Secret Source Plugin for Hermes Agent

Feature: `0001-base` · Plan: [02-plan.md](02-plan.md) · Status: **implemented**

Conventions: TDD per constitution — for every task the tests are written first,
then the implementation, then run locally until green. A task is done only when
its tests pass and coverage on the touched module stays ≥80%.

Final state: 145 tests passing, 97% total coverage (every module ≥96%).

## Phase 0 — scaffolding & risk burn-down

- [✓] #1 → Repo scaffolding (plugin.yaml, package skeleton, requirements-dev,
  .gitignore) — done; `import infisical_plugin` works from repo root.
- [✓] #2 → `scripts/fetch-hermes.sh` — done; pinned to hermes-agent
  `4281151ae859`; idempotent (verified by double run).
- [✓] #3 → `tests/conftest.py` — done; `.deps` on sys.path, conformance kit
  loaded by file path (no `tests` package collision).
- [✓] #4 → Walking skeleton — done with adaptation: a full Hermes install was
  impractical here, so `tests/test_plugin_loading.py` drives the *actual*
  loader (`PluginManager._load_directory_module` from the pinned checkout),
  which is the identical code path; both sources register through it.
- [✓] #5 → CI workflow — done; pytest + `--cov-fail-under=80` (local run of
  the same command is green; hosted run pending first push).

## Phase 1 — pure library

- [✓] #6 → `errors.py` — done; uniform `infisical: <kind>: <message>` shape.
- [✓] #7 → `refs.py` — done; full grammar incl. dynamic options, unknown/
  repeated/empty options rejected, percent-decoding, 11-case invalid matrix.
- [✓] #8 → `auth.py` agent method — done; missing/empty sink →
  NOT_CONFIGURED naming the checked path.
- [✓] #9 → `auth.py` universal-auth — done; env-var and file sourcing,
  single login call, 400/401/403 → AUTH_FAILED, network/timeout mapped.
- [✓] #10 → `auth.py` static token + dispatch — done; unknown method and
  malformed auth section → NOT_CONFIGURED.
- [✓] #11 → `client.py` Deadline — done; monotonic, clamps per-call timeout,
  exhausted budget raises TIMEOUT.
- [✓] #12 → `client.py` session + status mapping — done; 401 (expired-aware),
  403, 5xx, connection, timeout all mapped; TLS verify not disableable.
- [✓] #13 → `client.py` get_secret — done; v4 endpoint, 404/empty →
  EMPTY_VALUE.
- [✓] #14 → `client.py` list_secrets — done with adaptation: the v4 list
  route has **no limit/offset pagination** (verified in the Infisical server
  source), so this is one deadline-guarded request; kept as a lazy generator
  (imports first, direct secrets win) so folding stays streaming.
- [✓] #15 → `client.py` resolve_project — done; endpoint verified against
  the Infisical server source (`GET /api/v1/projects`, identity-token auth,
  `{projects: [{id, name, slug, orgId}]}`) instead of a live instance;
  UUID fast path, process-lifetime cache, ambiguous/missing names handled.
- [✓] #16 → `client.py` create_dynamic_lease — done; body/ttl asserted,
  404 and empty payload → EMPTY_VALUE.

## Phase 2 — Hermes adapter layer

- [✓] #17 → `InfisicalSource` identity + hooks — done; schema covers all
  spec §5 keys; protected vars as specified; override_existing default True.
- [✓] #18 → mapped `fetch()` — done; per-ref failures warn, fatal kinds
  (auth/network/timeout/misconfig) abort and clear partial secrets;
  organization validation included.
- [✓] #19 → dynamic refs — done; field selection incl. single-field
  shortcut, multi-field/unknown-field errors list available fields;
  project-ID dynamic refs rejected with actionable message.
- [✓] #20 → `InfisicalBulkSource` — done; direct-wins-over-imports, invalid
  names/empty values warned and skipped, recursive/include_imports pass-through.
- [✓] #21 → Root `__init__.py` real wiring — done; registration exceptions
  swallowed (asserted via exploding-context test).

## Phase 3 — conformance, hardening, docs

- [✓] #22 → Conformance — done; Hermes' kit passes unmodified for both
  sources (hermetic HOME + env isolation fixture).
- [✓] #23 → Security tests — done; token/client-secret/value leak assertions
  across the failure matrix for both sources and universal-auth.
- [✓] #24 → Scalability — done; 100k-secret bulk fetch ~seconds, tracemalloc
  peak bounded, both asserted.
- [✓] #25 → Timeout behavior — done; read-timeout mapping and budget
  exhaustion both return clean TIMEOUT results within the source budget.
- [✓] #26 → README — done; doc test asserts every `config_schema()` key (and
  auth sub-keys, protected vars) appears in the README config reference.
- [✓] #27 → End-to-end — done with adaptation: no live Infisical credentials
  in this environment, so E2E = real registry `apply_all()` round trip with
  mocked API (mapped-beats-bulk asserted in the applied environ); the live
  checklist ships in README for a credentialed run.
- [✓] #28 → Final pass — /code-review (high effort, 8 finder angles) run on
  the branch. Fixed: URL-encoding of secret keys in request paths; project
  cache now partitioned by auth identity; organization check no longer
  bypassable via raw project UUIDs (and dynamic refs by UUID now resolve
  their slug); single-binding failures promoted to hard errors matching the
  spec Gherkin (all-invalid maps, sole missing secret, ambiguous dynamic
  field); per-resource 403s degrade to warnings instead of sinking the whole
  fetch; 429/502/503/504 classified NETWORK; universal-auth malformed-JSON
  crash fixed; duplicated fetch() boundary and protected_env_vars unified in
  a shared base class with the uniform "infisical:" error prefix; login now
  reuses the client's pooled session; duplicated test fixtures consolidated
  into conftest. Deferred with rationale: per-ref request batching/
  concurrency (config sizes are small; plan accepted per-ref GETs) and the
  AUTH_EXPIRED body-text heuristic (mirrors the bundled 1Password source's
  best-effort classification). Coverage after fixes: 97%.
