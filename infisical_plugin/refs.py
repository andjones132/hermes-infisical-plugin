"""Parsing of ``inf://`` secret references.

Grammar (spec §4)::

    inf://<project>/<environment>[/<folder>...]/<key>[?<options>]

Options: ``type=dynamic``, ``field=<name>``, ``ttl=<duration>`` — the latter
two only meaningful for dynamic refs. Unknown options are rejected rather
than ignored so a typo can't silently change behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, unquote

from .errors import ErrorKind, InfisicalError

SCHEME_PREFIX = "inf://"

_EXPECTED = "inf://<project>/<environment>[/<folder>...]/<key>[?type=dynamic&field=...&ttl=...]"

_ALLOWED_OPTIONS = {"type", "field", "ttl"}


@dataclass(frozen=True)
class Ref:
    project: str
    environment: str
    secret_path: str  # normalized, always starts with "/"
    key: str
    dynamic: bool = False
    field: Optional[str] = None
    ttl: Optional[str] = None


def _invalid(detail: str) -> InfisicalError:
    return InfisicalError(
        ErrorKind.REF_INVALID, f"{detail} (expected {_EXPECTED})"
    )


def parse_ref(text: str) -> Ref:
    """Parse a reference string, raising ``InfisicalError(REF_INVALID)``."""
    if not isinstance(text, str):
        raise _invalid("reference is not a string")
    cleaned = text.strip()
    if not cleaned.startswith(SCHEME_PREFIX):
        raise _invalid(f"{cleaned!r} does not start with {SCHEME_PREFIX}")

    rest = cleaned[len(SCHEME_PREFIX):]
    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)

    segments = [unquote(seg) for seg in rest.split("/")]
    if any(not seg for seg in segments):
        raise _invalid(f"{cleaned!r} contains an empty path segment")
    if len(segments) < 3:
        raise _invalid(
            f"{cleaned!r} needs at least project, environment and key"
        )

    project, environment = segments[0], segments[1]
    folders = segments[2:-1]
    key = segments[-1]
    secret_path = "/" + "/".join(folders) if folders else "/"

    options = _parse_options(cleaned, query)
    dynamic = options.get("type") == "dynamic"
    field = options.get("field")
    ttl = options.get("ttl")
    if not dynamic and (field is not None or ttl is not None):
        raise _invalid(
            f"{cleaned!r} uses field/ttl options without type=dynamic"
        )

    return Ref(
        project=project,
        environment=environment,
        secret_path=secret_path,
        key=key,
        dynamic=dynamic,
        field=field,
        ttl=ttl,
    )


def _parse_options(ref_text: str, query: str) -> dict:
    if not query:
        return {}
    options: dict = {}
    for name, value in parse_qsl(query, keep_blank_values=True):
        if name not in _ALLOWED_OPTIONS:
            raise _invalid(f"{ref_text!r} has unknown option {name!r}")
        if name in options:
            raise _invalid(f"{ref_text!r} repeats option {name!r}")
        if not value:
            raise _invalid(f"{ref_text!r} has empty option {name!r}")
        options[name] = value
    if "type" in options and options["type"] != "dynamic":
        raise _invalid(
            f"{ref_text!r} has invalid type {options['type']!r} "
            "(only type=dynamic is supported)"
        )
    return options
