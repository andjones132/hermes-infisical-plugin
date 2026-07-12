"""Task #4: the real Hermes loader can import this plugin directory.

Drives ``PluginManager._load_directory_module`` from the pinned checkout —
the exact code path a real Hermes install uses for ``~/.hermes/plugins/*`` —
against this repository, then exercises ``register(ctx)``.
"""

from __future__ import annotations

import sys

from tests.conftest import REPO_ROOT


class FakeContext:
    def __init__(self):
        self.registered = []

    def register_secret_source(self, source):
        self.registered.append(source)


def _load_via_real_loader():
    from hermes_cli.plugins import PluginManager, PluginManifest

    manifest = PluginManifest(name="infisical", path=str(REPO_ROOT))
    # _load_directory_module does not touch self; call it unbound so the
    # test needs no PluginManager construction (which wants a Hermes home).
    module = PluginManager._load_directory_module(None, manifest)
    return module


def test_real_loader_imports_plugin_and_registers_sources():
    sys.modules.pop("hermes_plugins.infisical", None)
    module = _load_via_real_loader()
    assert hasattr(module, "register")

    ctx = FakeContext()
    module.register(ctx)
    names = sorted(s.name for s in ctx.registered)
    assert names == ["infisical", "infisical_bulk"]


def test_register_swallows_registration_errors():
    sys.modules.pop("hermes_plugins.infisical", None)
    module = _load_via_real_loader()

    class ExplodingContext:
        def register_secret_source(self, source):
            raise RuntimeError("boom")

    module.register(ExplodingContext())  # must not raise
