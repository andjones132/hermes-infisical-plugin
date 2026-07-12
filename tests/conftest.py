"""Test bootstrap: make the pinned Hermes checkout importable.

Puts ``.deps/hermes-agent`` on ``sys.path`` so ``agent.secret_sources.base``
resolves, and loads Hermes' conformance kit by file path — importing it as
``tests.secret_sources.conformance`` would collide with this repo's own
``tests`` package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HERMES_ROOT = REPO_ROOT / ".deps" / "hermes-agent"

if not HERMES_ROOT.is_dir():
    raise RuntimeError(
        f"Missing Hermes checkout at {HERMES_ROOT}. "
        "Run scripts/fetch-hermes.sh first."
    )

# REPO_ROOT must win any name collision with the vendored checkout (both
# trees have a top-level ``tests`` package) — insert it at the front.
# HERMES_ROOT is appended instead of inserted: it only needs to be
# importable (agent.secret_sources.*), never to shadow this repo's modules.
# ``path not in sys.path`` alone isn't enough to guarantee ordering — under
# ``python -m pytest`` the interpreter has already put REPO_ROOT (cwd) on
# sys.path, so a plain insert(0, HERMES_ROOT) would still land before it.
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))
if str(HERMES_ROOT) not in sys.path:
    sys.path.append(str(HERMES_ROOT))


import pytest  # noqa: E402  (after sys.path bootstrap)


@pytest.fixture(autouse=True)
def clean_project_cache():
    """Reset the process-lifetime project cache around every test."""
    from infisical_plugin.client import _reset_project_cache_for_tests

    _reset_project_cache_for_tests()
    yield
    _reset_project_cache_for_tests()


def load_hermes_conformance():
    """Load Hermes' conformance module by file path (avoids name collision)."""
    conformance_path = HERMES_ROOT / "tests" / "secret_sources" / "conformance.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_secret_source_conformance", conformance_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
