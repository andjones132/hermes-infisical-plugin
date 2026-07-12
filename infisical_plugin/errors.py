"""Error taxonomy for the Infisical plugin.

Internal layers raise :class:`InfisicalError`; ``sources.py`` is the only
place that converts them into Hermes ``FetchResult`` errors — the
never-raise boundary lives there and nowhere else.

Messages must be safe to surface: reference strings and env-var *names*
only — never secret values, never tokens.
"""

from __future__ import annotations

from agent.secret_sources.base import ErrorKind

__all__ = ["ErrorKind", "InfisicalError", "format_error"]


def format_error(kind: ErrorKind, message: str) -> str:
    """Uniform user-facing shape shared by every error and warning."""
    return f"infisical: {kind.value}: {message}"


class InfisicalError(Exception):
    """A classified plugin failure, formatted uniformly.

    ``resource_scoped`` marks failures confined to one secret/resource
    (e.g. a 403 on a single path under least-privilege policies) so the
    fetch loop can degrade them to per-reference warnings instead of
    aborting the whole run.
    """

    def __init__(self, kind: ErrorKind, message: str, *, resource_scoped: bool = False):
        self.kind = kind
        self.message = message
        self.resource_scoped = resource_scoped
        super().__init__(format_error(kind, message))
