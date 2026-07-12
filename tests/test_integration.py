"""Task #27 (code-level): full orchestrator round trip.

Registers both sources with Hermes' real registry and runs its
``apply_all`` against a mocked Infisical API, asserting the secret
actually lands in the environment mapping — the closest to a live E2E
that runs hermetically. The live-instance checklist lives in README.md.
"""

from pathlib import Path

import pytest
import responses

from agent.secret_sources.registry import (
    _reset_registry_for_tests,
    apply_all,
    register_source,
)

from infisical_plugin.client import _reset_project_cache_for_tests
from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

HOST = "https://infisical.example.com"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    _reset_project_cache_for_tests()
    _reset_registry_for_tests()
    monkeypatch.setattr(
        "agent.secret_sources.registry._ensure_builtin_sources", lambda: None
    )
    yield
    _reset_registry_for_tests()
    _reset_project_cache_for_tests()


@responses.activate
def test_orchestrator_applies_mapped_and_bulk(tmp_path):
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": "id-1", "name": "P", "slug": "p", "orgId": "o"}]},
    )
    responses.get(
        f"{HOST}/api/v4/secrets/MAPPED_KEY",
        json={"secret": {"secretValue": "mapped-value"}},
    )
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={"secrets": [{"secretKey": "BULK_KEY", "secretValue": "bulk-value"},
                          {"secretKey": "MAPPED_KEY", "secretValue": "bulk-loses"}],
              "imports": []},
    )

    sink = tmp_path / "agent-token"
    sink.write_text("tok")
    auth = {"method": "agent", "agent_token_path": str(sink)}
    config = {
        "infisical": {
            "enabled": True,
            "host_url": HOST,
            "auth": auth,
            "env": {"MAPPED_KEY": "inf://p/prod/MAPPED_KEY"},
        },
        "infisical_bulk": {
            "enabled": True,
            "host_url": HOST,
            "auth": auth,
            "project": "p",
            "environment": "prod",
        },
    }

    assert register_source(InfisicalSource())
    assert register_source(InfisicalBulkSource())

    environ: dict = {}
    report = apply_all(config, tmp_path, environ=environ)

    assert environ["MAPPED_KEY"] == "mapped-value"  # mapped beats bulk
    assert environ["BULK_KEY"] == "bulk-value"
    names = {sr.name for sr in report.sources}
    assert names == {"infisical", "infisical_bulk"}
