"""Task #25: a hung API produces a clean TIMEOUT FetchResult, inside the
source's own budget (before the orchestrator's hard kill)."""

import time
from pathlib import Path

import pytest
import requests
import responses

from agent.secret_sources.base import ErrorKind

from infisical_plugin.sources import InfisicalSource

HOST = "https://infisical.example.com"


def make_cfg(tmp_path, timeout_seconds):
    sink = tmp_path / "agent-token"
    sink.write_text("tok")
    return {
        "enabled": True,
        "host_url": HOST,
        "auth": {"method": "agent", "agent_token_path": str(sink)},
        "env": {"K": "inf://p/prod/K"},
        "timeout_seconds": timeout_seconds,
    }


@responses.activate
def test_read_timeout_maps_to_timeout_kind(tmp_path):
    responses.get(f"{HOST}/api/v1/projects", body=requests.Timeout("hung"))
    result = InfisicalSource().fetch(make_cfg(tmp_path, 120), Path("/tmp"))
    assert result.error_kind is ErrorKind.TIMEOUT
    assert not result.secrets


@responses.activate
def test_budget_exhaustion_returns_clean_timeout_within_budget(tmp_path):
    """First call eats the whole budget; the next call must fail fast."""
    budget = 0.4  # -> internal deadline of 0.2s (see _budget margin logic)

    def slow_projects(request):
        time.sleep(0.3)
        return (200, {}, '{"projects": [{"id": "id-1", "name": "P", '
                         '"slug": "p", "orgId": "o"}]}')

    responses.add_callback(
        responses.GET, f"{HOST}/api/v1/projects", callback=slow_projects,
        content_type="application/json",
    )

    started = time.monotonic()
    result = InfisicalSource().fetch(make_cfg(tmp_path, budget), Path("/tmp"))
    elapsed = time.monotonic() - started

    assert result.error_kind is ErrorKind.TIMEOUT
    assert "budget" in result.error
    assert not result.secrets
    assert elapsed < budget + 1.0  # returned promptly, no hard-kill needed
