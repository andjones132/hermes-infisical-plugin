"""Task #22: Hermes' own conformance kit, run against both sources.

The kit is loaded by file path (see conftest) to avoid colliding with this
repo's ``tests`` package. HOME is redirected and INFISICAL_* env vars are
cleared so the kit's enabled-but-unconfigured probes stay hermetic even on
machines that have a real agent sink or credentials.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_hermes_conformance

from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

conformance = load_hermes_conformance()


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("INFISICAL_TOKEN", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


class TestInfisicalSourceConformance(conformance.SecretSourceConformance):
    @pytest.fixture
    def source(self):
        return InfisicalSource()


class TestInfisicalBulkSourceConformance(conformance.SecretSourceConformance):
    @pytest.fixture
    def source(self):
        return InfisicalBulkSource()
