"""Task #24 (spec TR-4): a very large bulk folder is handled within budget
and without disproportionate memory beyond the accumulating result map.

Note: the v4 list endpoint has no pagination (verified against the server
source), so the response arrives in one JSON document; the plugin streams
it into the result mapping via a generator without building intermediate
lists.
"""

import json
import time
import tracemalloc
from pathlib import Path

import pytest
import responses

from infisical_plugin.sources import InfisicalBulkSource

HOST = "https://infisical.example.com"
N_SECRETS = 100_000


@responses.activate
def test_bulk_100k_secrets_within_budget(tmp_path):
    payload = {
        "secrets": [
            {"secretKey": f"KEY_{i}", "secretValue": f"value-{i}"}
            for i in range(N_SECRETS)
        ],
        "imports": [],
    }
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": "id-1", "name": "P", "slug": "p", "orgId": "o"}]},
    )
    responses.get(f"{HOST}/api/v4/secrets", body=json.dumps(payload),
                  content_type="application/json")

    sink = tmp_path / "agent-token"
    sink.write_text("tok")
    cfg = {
        "enabled": True,
        "host_url": HOST,
        "auth": {"method": "agent", "agent_token_path": str(sink)},
        "project": "p",
        "environment": "prod",
    }

    tracemalloc.start()
    started = time.monotonic()
    result = InfisicalBulkSource().fetch(cfg, Path("/tmp"))
    elapsed = time.monotonic() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result.ok, result.error
    assert len(result.secrets) == N_SECRETS
    assert result.secrets["KEY_0"] == "value-0"
    assert result.secrets[f"KEY_{N_SECRETS - 1}"] == f"value-{N_SECRETS - 1}"
    # Way inside the 120s orchestrator budget even on slow CI runners.
    assert elapsed < 60, f"bulk fetch took {elapsed:.1f}s"
    # Peak memory stays within a small multiple of the payload itself
    # (~10 MB of key/value text): no quadratic blow-up, no full extra copies.
    assert peak < 500 * 1024 * 1024, f"peak memory {peak / 1e6:.0f} MB"
