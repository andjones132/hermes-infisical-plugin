"""Task #23: no token, client secret, or secret value ever surfaces in
``error`` or ``warnings`` strings, across the failure matrix (spec TR-5)."""

from pathlib import Path

import pytest
import responses

from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

HOST = "https://infisical.example.com"
TOKEN = "LEAK-token-value"
SECRET_VALUE = "LEAK-secret-value"
CLIENT_SECRET = "LEAK-client-secret"

PROJECTS = {
    "projects": [
        {"id": "id-1", "name": "P", "slug": "p", "orgId": "org-1"}
    ]
}


def assert_no_leaks(result):
    surfaced = " ".join([result.error or "", *result.warnings])
    for sensitive in (TOKEN, SECRET_VALUE, CLIENT_SECRET):
        assert sensitive not in surfaced, f"{sensitive!r} leaked: {surfaced}"


def agent_cfg(tmp_path, **extra):
    sink = tmp_path / "agent-token"
    sink.write_text(TOKEN)
    cfg = {
        "enabled": True,
        "host_url": HOST,
        "auth": {"method": "agent", "agent_token_path": str(sink)},
    }
    cfg.update(extra)
    return cfg


FAILURE_RESPONSES = [
    ("auth", lambda: responses.get(f"{HOST}/api/v1/projects", body="no", status=401)),
    ("server-error", lambda: responses.get(
        f"{HOST}/api/v1/projects", body=f"oops {SECRET_VALUE}", status=500)),
    ("not-found", lambda: (
        responses.get(f"{HOST}/api/v1/projects", json=PROJECTS),
        responses.get(f"{HOST}/api/v4/secrets/K", json={}, status=404),
    )),
    ("empty-value", lambda: (
        responses.get(f"{HOST}/api/v1/projects", json=PROJECTS),
        responses.get(f"{HOST}/api/v4/secrets/K",
                      json={"secret": {"secretValue": ""}}),
    )),
    ("multi-field-dynamic", lambda: (
        responses.get(f"{HOST}/api/v1/projects", json=PROJECTS),
        responses.post(f"{HOST}/api/v1/dynamic-secrets/leases",
                       json={"lease": {}, "data": {"a": SECRET_VALUE,
                                                   "b": SECRET_VALUE}}),
    )),
]


@responses.activate
@pytest.mark.parametrize("name,setup", FAILURE_RESPONSES, ids=[f[0] for f in FAILURE_RESPONSES])
def test_mapped_failures_never_leak(tmp_path, name, setup):
    setup()
    ref = "inf://p/prod/K?type=dynamic" if name == "multi-field-dynamic" else "inf://p/prod/K"
    cfg = agent_cfg(tmp_path, env={"K": ref})
    result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert_no_leaks(result)


@responses.activate
def test_bulk_failures_never_leak(tmp_path):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={"secrets": [{"secretKey": "BAD NAME", "secretValue": SECRET_VALUE},
                          {"secretKey": "EMPTY", "secretValue": ""}],
              "imports": []},
    )
    cfg = agent_cfg(tmp_path, project="p", environment="prod")
    result = InfisicalBulkSource().fetch(cfg, Path("/tmp"))
    assert_no_leaks(result)


def test_universal_auth_failure_never_leaks(tmp_path, monkeypatch):
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", CLIENT_SECRET)
    with responses.RequestsMock() as mock:
        mock.post(f"{HOST}/api/v1/auth/universal-auth/login", body="no", status=401)
        cfg = {
            "enabled": True,
            "host_url": HOST,
            "auth": {"method": "universal-auth"},
            "env": {"K": "inf://p/prod/K"},
        }
        result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert_no_leaks(result)
