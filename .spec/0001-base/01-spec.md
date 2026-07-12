# Spec: Infisical Secret Source Plugin for Hermes Agent

Feature: `0001-base` · Status: **draft — awaiting approval**

## 1. Problem

Hermes Agent resolves provider credentials into environment variables at process
startup via *secret source* plugins. There is no plugin for
[Infisical](https://infisical.com), so Hermes users who keep secrets in Infisical
must copy values into dotenv files by hand — insecure, error-prone, and stale.

## 2. Solution

A Hermes secret-source plugin (`~/.hermes/plugins/infisical/`) that implements the
`SecretSource` contract from `agent.secret_sources.base` and resolves secrets from
an Infisical instance (cloud US/EU or self-hosted) over its REST API.

Two sources are registered from one plugin:

| Source name      | Shape    | Purpose                                                                 |
|------------------|----------|-------------------------------------------------------------------------|
| `infisical`      | `mapped` | User binds env-var names to explicit secret references (has precedence) |
| `infisical_bulk` | `bulk`   | Injects an entire project/environment/folder of secrets implicitly      |

### 2.1 Constraint discovered during research

The Hermes `SecretSource` contract is **read-only and startup-only**: `fetch(cfg,
home_path) -> FetchResult` runs once, synchronously, at process start; there is no
runtime hook. Consequences:

- **Read** and **bulk/list injection** map directly onto `mapped` and `bulk`
  shapes; "listing available values" is delivered as bulk injection of a
  project/environment/folder.
- **Dynamic secrets** fit as a lease created *during* `fetch()`; the generated
  credentials are injected as env vars. The lease TTL must outlive the agent
  session — the plugin cannot renew leases after startup (documented limitation).

## 3. User stories

- **US-1 (read, mapped):** As a Hermes user, I map `OPENAI_API_KEY` to an Infisical
  reference (project → environment → folder → key) in my Hermes YAML config so the
  agent starts with the secret injected, without any secret material on disk.
- **US-2 (auth via Infisical Agent):** As an operator running the Infisical Agent
  locally, I point the plugin at the agent's token *sink file* so the plugin reuses
  the agent-managed, auto-renewed access token and needs no credentials of its own.
- **US-3 (auth via Machine Identity):** As an operator without a local Infisical
  Agent, I supply a Universal Auth client ID/secret (env vars or file paths) and
  the plugin logs in itself to obtain a short-lived access token.
- **US-4 (bulk):** As a Hermes user, I enable `infisical_bulk` for one
  project/environment/folder so every secret in it becomes an env var, yielding to
  mapped sources on conflicts.
- **US-5 (dynamic secrets):** As a user, I map env vars to a dynamic-secret
  reference so `fetch()` creates a lease at startup and injects the generated
  credentials (e.g. ephemeral DB username/password).

## 4. Secret reference format

Scheme: `inf` (registered via the `scheme` class attribute; unique across sources).

```
inf://<project>/<environment>/<secret-path>/<key>[?<options>]
```

- `<project>` — Infisical project **name or slug**; names are resolved to IDs via
  the API and cached for the process lifetime.
- `<environment>` — environment slug (`dev`, `staging`, `prod`, …).
- `<secret-path>` — folder path, zero or more segments (omitted ⇒ `/`).
- `<key>` — the secret key name (final segment).
- Options (query string):
  - `type=dynamic` — treat `<key>` as a dynamic-secret name; create a lease.
  - `field=<name>` — for dynamic secrets: which field of the generated `data`
    payload to inject (required when the payload has >1 field).
  - `ttl=<duration>` — dynamic lease TTL (e.g. `4h`); defaults to the dynamic
    secret's configured default.

Examples:

```
inf://my-project/prod/backend/OPENAI_API_KEY
inf://my-project/prod/db/pg-creds?type=dynamic&field=password&ttl=8h
```

The organization is **not** part of the reference: an Infisical access token is
already organization-scoped. An optional `organization` config key (the org
**ID**) exists purely for validation (fail fast with a clear error if a
resolved project belongs to a different org). When set, project references —
including raw project IDs — are always fully resolved so the check cannot be
bypassed.

## 5. Configuration

```yaml
secrets:
  sources: [infisical, infisical_bulk]

  infisical:
    enabled: true
    host_url: https://us.infisical.com        # or EU / self-hosted URL
    organization: ""                          # optional org *ID*, validation only
    auth:
      method: agent                           # agent | universal-auth | token
      # method: agent
      agent_token_path: ~/.infisical/agent-token   # Infisical Agent sink file
      # method: universal-auth  (values may also come from env vars
      # INFISICAL_CLIENT_ID / INFISICAL_CLIENT_SECRET, or from file paths)
      client_id_path: /etc/infisical/client-id
      client_secret_path: /etc/infisical/client-secret
      # method: token — static token from env var INFISICAL_TOKEN
    env:                                      # mapped bindings
      OPENAI_API_KEY: inf://my-project/prod/backend/OPENAI_API_KEY
      DB_PASSWORD: inf://my-project/prod/db/pg-creds?type=dynamic&field=password

  infisical_bulk:
    enabled: true
    # host_url / auth: same keys as above — Hermes hands each source only its
    # own section, so use YAML anchors (&auth / *auth) to share the block
    project: my-project
    environment: prod
    secret_path: /backend
    recursive: false
    include_imports: true
```

All configuration keys are declared via `config_schema()` so Hermes setup surfaces
can render them.

## 6. Acceptance criteria (Gherkin)

```gherkin
Feature: Mapped secret resolution

  Scenario: Resolve a mapped secret by project/environment/path/key
    Given a mapped binding OPENAI_API_KEY -> "inf://proj/prod/backend/OPENAI_API_KEY"
    And valid authentication is configured
    When fetch() runs
    Then result.secrets contains OPENAI_API_KEY with the value stored in Infisical
    And result.error is empty

  Scenario: Project referenced by display name
    Given the reference uses the project display name instead of the slug
    When fetch() runs
    Then the plugin resolves the name to the project ID via the API
    And the secret is returned

  Scenario: Invalid reference format
    Given a binding whose reference is "inf://only-a-project"
    When fetch() runs
    Then result.error_kind is REF_INVALID
    And result.error names the offending env var and the expected format

  Scenario: Secret does not exist
    Given a reference to a key absent from Infisical
    When fetch() runs
    Then result.error_kind is EMPTY_VALUE
    And no exception propagates

  Scenario: One bad reference among many becomes a warning
    Given one binding that fails (bad ref, missing key, or per-path 403)
    And other bindings that resolve successfully
    When fetch() runs
    Then the successful secrets are returned with ok status
    And the failure appears in result.warnings
    # Failures are promoted to result.error only when NOTHING resolved.

Feature: Authentication

  Scenario: Authenticate via Infisical Agent token sink
    Given auth.method is "agent" and agent_token_path points to a file containing a valid token
    When fetch() runs
    Then API requests carry "Authorization: Bearer <token from file>"

  Scenario: Agent token file missing
    Given auth.method is "agent" and the sink file does not exist
    When fetch() runs
    Then result.error_kind is NOT_CONFIGURED
    And result.error states the path that was checked

  Scenario: Authenticate via Machine Identity (Universal Auth)
    Given auth.method is "universal-auth" with a valid client ID and secret
    When fetch() runs
    Then the plugin POSTs to /api/v1/auth/universal-auth/login once
    And uses the returned access token for all subsequent requests in the run

  Scenario: Rejected credentials
    Given Infisical rejects the login or token with 401
    When fetch() runs
    Then result.error_kind is AUTH_FAILED

  Scenario: Expired agent token
    Given Infisical rejects the sink-file token as expired
    When fetch() runs
    Then result.error_kind is AUTH_EXPIRED

Feature: Bulk injection

  Scenario: Inject an entire folder
    Given infisical_bulk is enabled for proj/prod path /backend containing 3 secrets
    When fetch() runs
    Then result.secrets contains exactly those 3 key/value pairs

  Scenario: Large folder is fully paginated
    Given the folder contains more secrets than one API page
    When fetch() runs
    Then all secrets are returned (pagination is followed to completion)

Feature: Dynamic secrets

  Scenario: Lease created at startup
    Given a binding DB_PASSWORD -> "inf://proj/prod/db/pg?type=dynamic&field=password&ttl=8h"
    When fetch() runs
    Then the plugin POSTs to /api/v1/dynamic-secrets/leases with ttl "8h"
    And result.secrets contains DB_PASSWORD set to data.password from the lease

  Scenario: Ambiguous field
    Given the lease payload has multiple fields and no field= option was given
    When fetch() runs
    Then result.error_kind is REF_INVALID
    And result.error lists the available field names

Feature: Contract conformance

  Scenario: Network unreachable
    Given the Infisical host cannot be reached
    When fetch() runs
    Then result.error_kind is NETWORK and no exception propagates

  Scenario: Timeout budget respected
    Given the API hangs
    When fetch() runs
    Then fetch() returns within fetch_timeout_seconds with error_kind TIMEOUT

  Scenario: Bootstrap credentials are protected
    Then protected_env_vars() includes INFISICAL_TOKEN, INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET
```

## 7. Technical requirements

- **TR-1 Hermes contract.** Implement `SecretSource` (`name`, `label`, `shape`,
  `scheme`, `fetch()`, `config_schema()`, `protected_env_vars()`,
  `fetch_timeout_seconds()`); provide `plugin.yaml` + `__init__.py` with
  `register(ctx)` registering both sources; pin `SecretSource.api_version`.
- **TR-2 Contract rules.** `fetch()` never raises, never prompts, never touches
  `os.environ`, returns within the timeout budget. All failures map to the Hermes
  `ErrorKind` taxonomy (`NOT_CONFIGURED`, `AUTH_FAILED`, `AUTH_EXPIRED`,
  `REF_INVALID`, `NETWORK`, `EMPTY_VALUE`, `TIMEOUT`, `INTERNAL`).
- **TR-3 Infisical API.** Use the REST API directly (no Infisical SDK dependency):
  - Login: `POST /api/v1/auth/universal-auth/login` (client ID/secret → access token).
  - Get secret: `GET /api/v4/secrets/{secretName}?projectId&environment&secretPath`.
  - List secrets: `GET /api/v4/secrets?projectId&environment&secretPath&recursive&includeImports`.
  - Dynamic lease: `POST /api/v1/dynamic-secrets/leases`.
  - Project name→ID resolution via the projects listing endpoint, cached per process.
- **TR-4 Scalability.** Bulk listing follows pagination to completion and streams
  results into the mapping; must handle folders with hundreds of thousands of
  records without exhausting memory or the timeout budget disproportionately.
- **TR-5 Security.** Secret values never appear in logs or error strings; error
  messages include references and env-var *names* only. Bootstrap credentials are
  declared in `protected_env_vars()`. TLS verification is on and not disableable.
- **TR-6 Testing (constitution).** TDD; unit tests for reference parsing, auth
  resolution, error mapping; integration tests against a mocked Infisical HTTP
  server; Hermes conformance-kit subclass (`SecretSourceConformance`); ≥80%
  coverage per module; explicit edge/error-case tests; no flaky tests.
- **TR-7 Consistency (constitution).** Both sources share one client and one
  reference grammar; error message format is uniform (`infisical: <ErrorKind>:
  <actionable message>`); docs match implementation exactly.
- **TR-8 Runtime.** Python, compatible with the Hermes Agent codebase; minimal
  third-party dependencies (prefer stdlib HTTP unless the plan justifies otherwise).

## 8. Out of scope (this feature)

- Writing, modifying, or deleting secrets — the plugin is strictly read-only.
- Lease renewal/revocation after startup (contract is startup-only).
- Infisical secret rotation, approvals, PIT recovery, secret referencing/imports
  authoring.
- Auth methods other than Agent sink, Universal Auth, and static token
  (Kubernetes/AWS/GCP/Azure identity auth may be a later feature).

## 9. Open questions

- **OQ-1:** Does Hermes pass the mapped `env:` bindings through `cfg` verbatim
  (assumed), and does the conformance kit require any additional hooks? Verify
  against the Hermes repository during planning.
- **OQ-2:** Default `agent_token_path` — Infisical Agent sinks are user-defined
  paths, so any default is a convention of ours; `~/.infisical/agent-token` is
  proposed.

## 10. References

- [Hermes Agent — Secret Source Plugin developer guide](https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin)
- [Infisical — Universal Auth (machine identities)](https://infisical.com/docs/documentation/platform/identities/universal-auth)
- [Infisical — Infisical Agent (auth + token sinks)](https://infisical.com/docs/integrations/platforms/infisical-agent)
- [Infisical API — retrieve secret](https://infisical.com/docs/api-reference/endpoints/secrets/read)
- [Infisical API — list secrets](https://infisical.com/docs/api-reference/endpoints/secrets/list)
- [Infisical API — create dynamic secret lease](https://infisical.com/docs/api-reference/endpoints/dynamic-secrets/create-lease)
