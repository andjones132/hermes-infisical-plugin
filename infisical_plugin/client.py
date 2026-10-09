"""Deadline-aware HTTP client for the Infisical REST API.

Endpoints used (verified against the Infisical server source,
backend/src/server/routes):

* ``GET  /api/v1/projects``                    — list projects (name→ID/slug)
* ``GET  /api/v4/secrets/{secretName}``        — get one secret
* ``GET  /api/v4/secrets``                     — list secrets at a path
  (the v4 list route has NO limit/offset pagination — the server returns
  the full set in one response; memory is bounded by that response and
  the wall-clock budget by :class:`Deadline`)
* ``POST /api/v1/dynamic-secrets/leases``      — create a dynamic-secret lease

TLS verification is always on; there is deliberately no knob to disable it.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, Iterator, Optional
from urllib.parse import quote

import requests

from .errors import ErrorKind, InfisicalError

DEFAULT_HOST_URL = "https://us.infisical.com"
PER_CALL_TIMEOUT_SECONDS = 30.0

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# HTTP statuses that signal a transient backend/transport condition — the
# taxonomy's NETWORK kind — rather than a plugin or request bug.
_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})

# Process-lifetime cache keyed by (host, identity fingerprint, requested
# name/slug/id).  The identity fingerprint keeps resolutions made under
# one token from being served to a source authenticating as a different
# identity in the same long-lived process.
_PROJECT_CACHE: Dict[tuple, "Project"] = {}


@dataclass
class Deadline:
    """Wall-clock budget for one fetch() run (monotonic-clock based)."""

    budget_seconds: float
    _started: float = dataclass_field(default_factory=time.monotonic)

    def remaining(self) -> float:
        return self.budget_seconds - (time.monotonic() - self._started)

    def per_call_timeout(
        self, per_call: float = PER_CALL_TIMEOUT_SECONDS
    ) -> float:
        """Timeout for the next HTTP call; raises TIMEOUT when exhausted."""
        remaining = self.remaining()
        if remaining <= 0:
            raise InfisicalError(
                ErrorKind.TIMEOUT,
                f"fetch budget of {self.budget_seconds:.0f}s exhausted",
            )
        return min(per_call, remaining)


@dataclass(frozen=True)
class Project:
    id: str
    slug: str
    name: str
    org_id: str


class InfisicalClient:
    def __init__(
        self,
        host_url: str,
        token: str,
        deadline: Deadline,
        session: Optional[requests.Session] = None,
    ):
        self.host_url = (host_url or DEFAULT_HOST_URL).rstrip("/")
        self.deadline = deadline
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"
        # Cache-partitioning fingerprint; the raw token never leaves the header.
        self._identity_fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self.host_url + path
        timeout = self.deadline.per_call_timeout()
        try:
            return self._session.request(method, url, timeout=timeout, **kwargs)
        except requests.Timeout as exc:
            raise InfisicalError(
                ErrorKind.TIMEOUT, f"{method} {path} timed out after {timeout:.0f}s"
            ) from exc
        except requests.RequestException as exc:
            raise InfisicalError(
                ErrorKind.NETWORK,
                f"{method} {path} failed to reach configured Infisical host; "
                "check host_url, network access, TLS trust, then retry",
            ) from exc

    def _check_status(
        self,
        resp: requests.Response,
        context: str,
        *,
        resource: bool = False,
        not_found: Optional[str] = None,
    ) -> None:
        """Map non-2xx statuses onto the shared ErrorKind taxonomy.

        ``resource=True`` marks calls addressing one specific secret or
        lease, where a 403 is a per-resource permission denial (common
        under least-privilege policies) rather than a broken token.
        ``not_found`` supplies the EMPTY_VALUE message for a 404.
        """
        status = resp.status_code
        if 200 <= status < 300:
            return
        if status == 404 and not_found is not None:
            raise InfisicalError(ErrorKind.EMPTY_VALUE, f"{context}: {not_found}")
        if status == 401:
            body = (resp.text or "").lower()
            kind = (
                ErrorKind.AUTH_EXPIRED if "expired" in body else ErrorKind.AUTH_FAILED
            )
            raise InfisicalError(
                kind, f"{context}: HTTP 401 — token rejected by {self.host_url}"
            )
        if status == 403:
            raise InfisicalError(
                ErrorKind.AUTH_FAILED,
                f"{context}: HTTP 403 — identity lacks permission",
                resource_scoped=resource,
            )
        if status in _TRANSIENT_STATUSES:
            raise InfisicalError(
                ErrorKind.NETWORK,
                f"{context}: transient HTTP {status} from {self.host_url}",
            )
        raise InfisicalError(
            ErrorKind.INTERNAL,
            f"{context}: unexpected HTTP {status} from {self.host_url}",
        )

    @staticmethod
    def _json(resp: requests.Response, context: str) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise InfisicalError(
                ErrorKind.INTERNAL, f"{context}: response was not valid JSON"
            ) from exc
        if not isinstance(data, dict):
            raise InfisicalError(
                ErrorKind.INTERNAL, f"{context}: unexpected response shape"
            )
        return data

    # -- projects -----------------------------------------------------------

    def resolve_project(
        self, name_or_slug_or_id: str, require_full: bool = False
    ) -> Project:
        """Resolve a project reference to (id, slug, name, org_id).

        A raw UUID normally skips the listing call and yields a Project
        with only ``id`` populated. Pass ``require_full=True`` when the
        caller needs slug/org fields (dynamic leases, organization
        validation) — the UUID is then matched against the listing like
        any other reference. Full resolutions are cached per identity
        for the process lifetime.
        """
        wanted = name_or_slug_or_id.strip()
        if _UUID_RE.match(wanted) and not require_full:
            # Cheap to construct; deliberately uncached so a later
            # require_full call for the same ID still resolves fully.
            return Project(id=wanted, slug="", name="", org_id="")

        cache_key = (self.host_url, self._identity_fp, wanted)
        cached = _PROJECT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        context = f"resolving project {wanted!r}"
        resp = self._request("GET", "/api/v1/projects")
        self._check_status(resp, context)
        projects = self._json(resp, context).get("projects")
        if not isinstance(projects, list):
            raise InfisicalError(
                ErrorKind.INTERNAL, f"{context}: response had no projects list"
            )

        by_id = {}
        by_slug = {}
        by_name = {}
        for item in projects:
            if not isinstance(item, dict):
                continue
            project = Project(
                id=str(item.get("id", "")),
                slug=str(item.get("slug", "")),
                name=str(item.get("name", "")),
                org_id=str(item.get("orgId", "")),
            )
            if project.id:
                by_id[project.id] = project
            if project.slug:
                by_slug[project.slug] = project
            if project.name:
                by_name.setdefault(project.name, []).append(project)

        match = by_id.get(wanted) or by_slug.get(wanted)
        if match is None:
            named = by_name.get(wanted, [])
            if len(named) > 1:
                raise InfisicalError(
                    ErrorKind.REF_INVALID,
                    f"project name {wanted!r} is ambiguous "
                    f"({len(named)} projects); use the project slug or ID",
                )
            match = named[0] if named else None
        if match is None:
            available = sorted(by_slug)
            raise InfisicalError(
                ErrorKind.REF_INVALID,
                f"no accessible project named {wanted!r} "
                f"(accessible project slugs: {', '.join(available) or 'none'})",
            )
        _PROJECT_CACHE[cache_key] = match
        return match

    # -- secrets ------------------------------------------------------------

    def get_secret(
        self, project_id: str, environment: str, secret_path: str, key: str
    ) -> str:
        context = f"getting secret {key!r} at {secret_path!r}"
        resp = self._request(
            "GET",
            f"/api/v4/secrets/{quote(key, safe='')}",
            params={
                "projectId": project_id,
                "environment": environment,
                "secretPath": secret_path,
                "viewSecretValue": "true",
                "expandSecretReferences": "true",
                "includeImports": "true",
            },
        )
        self._check_status(
            resp,
            context,
            resource=True,
            not_found=f"not found in environment {environment!r}",
        )
        secret = self._json(resp, context).get("secret")
        value = secret.get("secretValue") if isinstance(secret, dict) else None
        if not isinstance(value, str) or not value:
            raise InfisicalError(
                ErrorKind.EMPTY_VALUE, f"{context}: backend returned an empty value"
            )
        return value

    def list_secrets(
        self,
        project_id: str,
        environment: str,
        secret_path: str = "/",
        recursive: bool = False,
        include_imports: bool = True,
    ) -> Iterator[dict]:
        """Yield ``{"secretKey": ..., "secretValue": ...}`` dicts.

        Imported secrets are yielded first so directly-defined secrets
        overwrite them when the caller folds the stream into a mapping
        (matching Infisical's own precedence).
        """
        context = f"listing secrets at {secret_path!r}"
        resp = self._request(
            "GET",
            "/api/v4/secrets",
            params={
                "projectId": project_id,
                "environment": environment,
                "secretPath": secret_path,
                "recursive": "true" if recursive else "false",
                "includeImports": "true" if include_imports else "false",
                "viewSecretValue": "true",
                "expandSecretReferences": "true",
            },
        )
        self._check_status(
            resp,
            context,
            not_found=f"path not found in environment {environment!r}",
        )
        data = self._json(resp, context)

        if include_imports:
            for imported in data.get("imports") or []:
                if isinstance(imported, dict):
                    for secret in imported.get("secrets") or []:
                        if isinstance(secret, dict):
                            yield secret
        secrets = data.get("secrets")
        if not isinstance(secrets, list):
            raise InfisicalError(
                ErrorKind.INTERNAL, f"{context}: response had no secrets list"
            )
        for secret in secrets:
            if isinstance(secret, dict):
                yield secret

    # -- dynamic secrets ------------------------------------------------------

    def create_dynamic_lease(
        self,
        project_slug: str,
        environment: str,
        secret_path: str,
        name: str,
        ttl: Optional[str] = None,
    ) -> dict:
        """Create a lease and return the generated credential payload."""
        context = f"creating dynamic-secret lease for {name!r}"
        body = {
            "dynamicSecretName": name,
            "projectSlug": project_slug,
            "environmentSlug": environment,
            "path": secret_path,
        }
        if ttl:
            body["ttl"] = ttl
        resp = self._request("POST", "/api/v1/dynamic-secrets/leases", json=body)
        self._check_status(
            resp,
            context,
            resource=True,
            not_found=f"dynamic secret not found at {secret_path!r}",
        )
        data = self._json(resp, context).get("data")
        if not isinstance(data, dict) or not data:
            raise InfisicalError(
                ErrorKind.EMPTY_VALUE, f"{context}: lease returned no credential data"
            )
        return data


def _reset_project_cache_for_tests() -> None:
    _PROJECT_CACHE.clear()
