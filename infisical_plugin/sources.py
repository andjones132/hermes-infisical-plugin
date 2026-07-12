"""Hermes SecretSource adapters for Infisical.

The only module in the package that knows about the Hermes contract.
``fetch()`` never raises: every internal layer may raise
:class:`InfisicalError`, and the single try/except boundary in
:class:`_BaseInfisicalSource` converts it into a ``FetchResult``.

Failure policy (mirrors the bundled 1Password source):

* per-reference failures (missing secret, bad ref, ambiguous dynamic
  field, a 403 scoped to one resource) become warnings — one bad entry
  never sinks the rest;
* whole-fetch failures (auth, network, timeout, misconfig) set
  ``result.error``/``error_kind`` and clear any partial secrets, since a
  non-ok result must not carry secrets;
* when *nothing* resolved and at least one reference failed, the first
  failure is promoted to ``result.error`` so a fully-broken config is a
  hard error, not a silent no-op.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from agent.secret_sources.base import (
    SECRET_SOURCE_API_VERSION,
    ErrorKind,
    FetchResult,
    SecretSource,
    is_valid_env_name,
)

from .auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    DEFAULT_AGENT_TOKEN_PATH,
    STATIC_TOKEN_ENV,
    resolve_token,
)
from .client import DEFAULT_HOST_URL, Deadline, InfisicalClient, Project
from .errors import InfisicalError, format_error
from .refs import Ref, parse_ref

# Error kinds that abort the whole fetch instead of one reference: they
# would fail identically for every remaining reference. An error marked
# resource_scoped (e.g. a 403 on one secret) is exempt.
_FATAL_KINDS = frozenset(
    {
        ErrorKind.NOT_CONFIGURED,
        ErrorKind.AUTH_FAILED,
        ErrorKind.AUTH_EXPIRED,
        ErrorKind.NETWORK,
        ErrorKind.TIMEOUT,
    }
)

# Leave headroom inside the orchestrator's budget so we return a clean
# TIMEOUT result before its hard kill; for budgets too small to spare the
# full margin, keep at least half the budget rather than none.
_BUDGET_MARGIN_SECONDS = 5.0

_AUTH_SCHEMA = {
    "auth": {
        "description": (
            "Auth section: method is one of 'agent' (read the Infisical "
            "Agent's token sink file), 'universal-auth' (machine identity "
            f"client ID/secret from {CLIENT_ID_ENV}/{CLIENT_SECRET_ENV} or "
            "client_id_path/client_secret_path files), or 'token' "
            f"(static {STATIC_TOKEN_ENV})"
        ),
        "default": {"method": "agent", "agent_token_path": DEFAULT_AGENT_TOKEN_PATH},
    },
    "host_url": {
        "description": "Infisical instance URL (US/EU cloud or self-hosted)",
        "default": DEFAULT_HOST_URL,
    },
    "organization": {
        "description": (
            "Optional organization ID; when set, resolved projects must "
            "belong to it (validation only)"
        ),
        "default": "",
    },
    "timeout_seconds": {
        "description": "Wall-clock budget for the whole fetch",
        "default": 120,
    },
}


class _BaseInfisicalSource(SecretSource):
    """Shared identity plumbing and the single never-raise boundary."""

    api_version = SECRET_SOURCE_API_VERSION

    def protected_env_vars(self, cfg: dict):
        return frozenset({STATIC_TOKEN_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV})

    def fetch(self, cfg: dict, home_path: Path) -> FetchResult:
        cfg = cfg if isinstance(cfg, dict) else {}
        result = FetchResult()
        try:
            self._fetch(cfg, result)
        except InfisicalError as err:
            result.secrets = {}
            result.error = str(err)
            result.error_kind = err.kind
        except Exception as exc:  # the never-raise boundary
            result.secrets = {}
            result.error = format_error(
                ErrorKind.INTERNAL, f"unexpected {type(exc).__name__} in {self.name}"
            )
            result.error_kind = ErrorKind.INTERNAL
        return result

    @abstractmethod
    def _fetch(self, cfg: dict, result: FetchResult) -> None:
        """Source-specific fetch; may raise InfisicalError."""

    # -- shared plumbing -----------------------------------------------------

    def _make_client(self, cfg: dict) -> Tuple[InfisicalClient, str]:
        """Resolve auth and build a client on one pooled session."""
        host_url = str(cfg.get("host_url") or DEFAULT_HOST_URL)
        deadline = _budget(self, cfg)
        session = requests.Session()
        token = resolve_token(
            cfg.get("auth"),
            host_url,
            session=session,
            timeout=deadline.per_call_timeout(),
        )
        organization = str(cfg.get("organization") or "")
        client = InfisicalClient(host_url, token, deadline, session=session)
        return client, organization


def _budget(source: SecretSource, cfg: dict) -> Deadline:
    budget = source.fetch_timeout_seconds(cfg)
    return Deadline(budget_seconds=max(budget - _BUDGET_MARGIN_SECONDS, budget / 2))


def _check_organization(project: Project, organization: str) -> None:
    """Fail fast when the resolved project belongs to a different org."""
    if organization and project.org_id and project.org_id != organization:
        raise InfisicalError(
            ErrorKind.NOT_CONFIGURED,
            f"project {project.slug or project.id!r} belongs to organization "
            f"{project.org_id!r}, not the configured {organization!r}",
        )


def _select_dynamic_field(ref: Ref, data: dict) -> str:
    """Pick the credential field from a dynamic-lease payload."""
    available = ", ".join(sorted(data))
    if ref.field is not None:
        if ref.field not in data:
            raise InfisicalError(
                ErrorKind.REF_INVALID,
                f"dynamic secret {ref.key!r} has no field {ref.field!r} "
                f"(available fields: {available})",
            )
        value = data[ref.field]
    elif len(data) == 1:
        value = next(iter(data.values()))
    else:
        raise InfisicalError(
            ErrorKind.REF_INVALID,
            f"dynamic secret {ref.key!r} returned multiple fields; add "
            f"field=<name> to the reference (available fields: {available})",
        )
    if not isinstance(value, str):
        value = str(value)
    if not value:
        raise InfisicalError(
            ErrorKind.EMPTY_VALUE,
            f"dynamic secret {ref.key!r} returned an empty value",
        )
    return value


class InfisicalSource(_BaseInfisicalSource):
    """Mapped source: env vars bound to explicit inf:// references."""

    name = "infisical"
    label = "Infisical"
    shape = "mapped"
    scheme = "inf"

    def override_existing(self, cfg: dict) -> bool:
        # An explicit VAR -> inf:// binding is the strongest user intent
        # there is (same default as the bundled 1Password mapped source).
        return bool(isinstance(cfg, dict) and cfg.get("override_existing", True))

    def config_schema(self) -> dict:
        return {
            "enabled": {"description": "Master switch", "default": False},
            "env": {
                "description": (
                    "Map of ENV_VAR -> inf://<project>/<environment>"
                    "[/<folder>...]/<key>[?type=dynamic&field=...&ttl=...]"
                ),
                "default": {},
            },
            "override_existing": {
                "description": "Resolved values overwrite .env/shell values",
                "default": True,
            },
            **_AUTH_SCHEMA,
        }

    def _fetch(self, cfg: dict, result: FetchResult) -> None:
        refs, first_invalid = self._validate_env_map(cfg.get("env"), result)
        if not refs:
            if first_invalid is not None:
                raise first_invalid
            raise InfisicalError(
                ErrorKind.NOT_CONFIGURED,
                "secrets.infisical.enabled is true but the env: map is "
                "empty. Add ENV_VAR: inf://project/environment/key entries.",
            )

        client, organization = self._make_client(cfg)
        first_failure: Optional[InfisicalError] = None
        for name in sorted(refs):
            ref = refs[name]
            try:
                result.secrets[name] = self._resolve_ref(client, organization, ref)
            except InfisicalError as err:
                if err.kind in _FATAL_KINDS and not err.resource_scoped:
                    raise
                result.warnings.append(f"{name}: {err}")
                first_failure = first_failure or err

        # A run that resolved nothing is a hard error, not a quiet no-op.
        if not result.secrets and first_failure is not None:
            raise first_failure

    @staticmethod
    def _validate_env_map(
        env_map, result: FetchResult
    ) -> Tuple[Dict[str, Ref], Optional[InfisicalError]]:
        """Return valid refs plus the first validation failure (for
        promotion to a hard error when nothing at all is usable)."""
        refs: Dict[str, Ref] = {}
        first_invalid: Optional[InfisicalError] = None
        if not isinstance(env_map, dict):
            if env_map is not None:
                first_invalid = InfisicalError(
                    ErrorKind.NOT_CONFIGURED,
                    "secrets.infisical.env is not a mapping of "
                    "ENV_VAR: inf://... entries",
                )
                result.warnings.append(str(first_invalid))
            return refs, first_invalid
        for name, text in env_map.items():
            if not is_valid_env_name(str(name)):
                err = InfisicalError(
                    ErrorKind.REF_INVALID, f"{name!r} is not a valid env-var name"
                )
                result.warnings.append(f"Skipping {name!r}: {err}")
                first_invalid = first_invalid or err
                continue
            try:
                refs[str(name)] = parse_ref(text)
            except InfisicalError as err:
                named = InfisicalError(
                    err.kind, f"invalid reference for {name!r}: {err.message}"
                )
                result.warnings.append(f"Skipping {name!r}: {err}")
                first_invalid = first_invalid or named
        return refs, first_invalid

    @staticmethod
    def _resolve_ref(client: InfisicalClient, organization: str, ref: Ref) -> str:
        # Dynamic leases need the project slug and org validation needs
        # org_id, so those cases resolve fully even for raw project IDs.
        require_full = ref.dynamic or bool(organization)
        project = client.resolve_project(ref.project, require_full=require_full)
        _check_organization(project, organization)
        if not ref.dynamic:
            return client.get_secret(
                project.id, ref.environment, ref.secret_path, ref.key
            )
        data = client.create_dynamic_lease(
            project.slug, ref.environment, ref.secret_path, ref.key, ref.ttl
        )
        return _select_dynamic_field(ref, data)


class InfisicalBulkSource(_BaseInfisicalSource):
    """Bulk source: injects a whole project/environment/folder."""

    name = "infisical_bulk"
    label = "Infisical (bulk)"
    shape = "bulk"
    scheme = None

    # override_existing inherits the base default (False): bulk yields.

    def config_schema(self) -> dict:
        return {
            "enabled": {"description": "Master switch", "default": False},
            "project": {
                "description": "Project name, slug, or ID to inject from",
                "default": "",
            },
            "environment": {
                "description": "Environment slug (dev, staging, prod, ...)",
                "default": "",
            },
            "secret_path": {
                "description": "Folder to inject from",
                "default": "/",
            },
            "recursive": {
                "description": "Also inject secrets from nested folders",
                "default": False,
            },
            "include_imports": {
                "description": "Include secrets imported into the folder",
                "default": True,
            },
            "override_existing": {
                "description": "Resolved values overwrite .env/shell values",
                "default": False,
            },
            **_AUTH_SCHEMA,
        }

    def _fetch(self, cfg: dict, result: FetchResult) -> None:
        project_ref = str(cfg.get("project") or "")
        environment = str(cfg.get("environment") or "")
        if not project_ref or not environment:
            raise InfisicalError(
                ErrorKind.NOT_CONFIGURED,
                "secrets.infisical_bulk needs both project: and environment: set",
            )
        secret_path = str(cfg.get("secret_path") or "/")

        client, organization = self._make_client(cfg)
        project = client.resolve_project(
            project_ref, require_full=bool(organization)
        )
        _check_organization(project, organization)

        for secret in client.list_secrets(
            project.id,
            environment,
            secret_path,
            recursive=bool(cfg.get("recursive", False)),
            include_imports=bool(cfg.get("include_imports", True)),
        ):
            key = secret.get("secretKey")
            value = secret.get("secretValue")
            if not isinstance(key, str) or not is_valid_env_name(key):
                result.warnings.append(
                    f"Skipping {key!r}: not a valid env-var name"
                )
                continue
            if not isinstance(value, str) or not value:
                result.warnings.append(f"Skipping {key!r}: empty value")
                continue
            result.secrets[key] = value
