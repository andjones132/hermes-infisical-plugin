"""Hermes plugin entry point for the Infisical secret source.

Loaded by Hermes' plugin manager as ``hermes_plugins.infisical`` (the loader
sets ``__path__``/``__package__`` so the relative import below resolves to
the sibling ``infisical_plugin`` package).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register both Infisical sources. Must never break Hermes startup."""
    try:
        from .infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

        ctx.register_secret_source(InfisicalSource())
        ctx.register_secret_source(InfisicalBulkSource())
    except Exception:
        logger.warning(
            "infisical plugin failed to register its secret sources",
            exc_info=True,
        )
