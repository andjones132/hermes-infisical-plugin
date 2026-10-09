"""Task #23: no token, client secret, or secret value ever surfaces in
``error`` or ``warnings`` strings, across the failure matrix (spec TR-5)."""

from pathlib import Path

import pytest
import requests
import responses

from agent.secret_sources.base import ErrorKind
from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

HOST = "https://infisical.example.com"
TOKEN = "LEAK-token-value"
SECRET_VALUE = "LEAK-secret-value"
CLIENT_SECRET = "LEAK-client-secret"
TRANSPORT_DIAGNOSTIC = "Authorization: Bearer " + TOKEN

PROJECTS = {
    "projects": [
        {"id": "id-1", "name": "P", "slug": "p", "orgId": "org-1"}
    ]
}


def assert_no_leaks(result):
    surfaced = " ".join([result.error or "", *result.warnings])
    for sensitive in (TOKEN, SECRET_VALUE, CLIENT_SECRET):
        assert sensitive not in surfaced, f"{sensitive!r} leaked: {surfaced}"


def assert_no_transport_echo(result):
    surfaced = " ".join([result.error or "", *result.warnings])
    assert TRANSPORT_DIAGNOSTIC not in surfaced
    assert "Bearer" not in surfaced
    assert "check host_url" in surfaced


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


def test_transport_exception_diagnostic_is_not_echoed(tmp_path, monkeypatch):
    def fail_request(*args, **kwargs):
        raise requests.ConnectionError(TRANSPORT_DIAGNOSTIC)

    monkeypatch.setattr(requests.Session, "request", fail_request)
    cfg = agent_cfg(tmp_path, env={"K": "inf://p/prod/K"})
    result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert result.error_kind is ErrorKind.NETWORK
    assert result.secrets == {}
    assert_no_leaks(result)
    assert_no_transport_echo(result)


def test_universal_auth_transport_exception_diagnostic_is_not_echoed(
    tmp_path, monkeypatch
):
    def fail_post(*args, **kwargs):
        raise requests.ConnectionError(TRANSPORT_DIAGNOSTIC + " " + CLIENT_SECRET)

    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(requests.Session, "post", fail_post)
    cfg = {
        "enabled": True,
        "host_url": HOST,
        "auth": {"method": "universal-auth"},
        "env": {"K": "inf://p/prod/K"},
    }
    result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert result.error_kind is ErrorKind.NETWORK
    assert_no_leaks(result)
    assert_no_transport_echo(result)


@pytest.mark.parametrize(
    "bad_ref",
    [
        SECRET_VALUE,
        "inf://p/prod/" + SECRET_VALUE + "?type=static",
        "inf://p/prod/K?unexpected=" + SECRET_VALUE,
    ],
)
def test_malformed_reference_diagnostic_is_structural_not_raw(tmp_path, bad_ref):
    cfg = agent_cfg(tmp_path, env={"K": bad_ref})
    result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert result.error_kind is ErrorKind.REF_INVALID
    assert result.secrets == {}
    assert_no_leaks(result)
    surfaced = " ".join([result.error or "", *result.warnings])
    assert bad_ref not in surfaced
    assert "expected inf://" in surfaced


@responses.activate
def test_malformed_reference_warning_does_not_echo_raw_value(tmp_path):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets/K",
        json={"secret": {"secretValue": "resolved-nonsecret"}},
    )
    cfg = agent_cfg(
        tmp_path,
        env={"BAD_REF": SECRET_VALUE, "GOOD_REF": "inf://p/prod/K"},
    )
    result = InfisicalSource().fetch(cfg, Path("/tmp"))
    assert result.error is None
    assert result.secrets == {"GOOD_REF": "resolved-nonsecret"}
    assert_no_leaks(result)
    surfaced = " ".join(result.warnings)
    assert SECRET_VALUE not in surfaced
    assert "BAD_REF" in surfaced
