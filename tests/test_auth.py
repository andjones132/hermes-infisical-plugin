import pytest
import responses

from agent.secret_sources.base import ErrorKind

from infisical_plugin.auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    STATIC_TOKEN_ENV,
    resolve_token,
)
from infisical_plugin.errors import InfisicalError

HOST = "https://infisical.example.com"
LOGIN_URL = f"{HOST}/api/v1/auth/universal-auth/login"


def kind_of(call, *args, **kwargs) -> ErrorKind:
    with pytest.raises(InfisicalError) as exc:
        call(*args, **kwargs)
    return exc.value.kind


# -- agent sink ------------------------------------------------------------


def test_agent_reads_token_from_sink_file(tmp_path):
    sink = tmp_path / "agent-token"
    sink.write_text("tok-123\n")
    cfg = {"method": "agent", "agent_token_path": str(sink)}
    assert resolve_token(cfg, HOST) == "tok-123"


def test_agent_is_the_default_method(tmp_path):
    sink = tmp_path / "agent-token"
    sink.write_text("tok-default")
    assert resolve_token({"agent_token_path": str(sink)}, HOST) == "tok-default"


def test_agent_missing_sink_is_not_configured_and_names_path(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(InfisicalError) as exc:
        resolve_token({"method": "agent", "agent_token_path": str(missing)}, HOST)
    assert exc.value.kind is ErrorKind.NOT_CONFIGURED
    assert str(missing) in str(exc.value)


def test_agent_empty_sink_is_not_configured(tmp_path):
    sink = tmp_path / "agent-token"
    sink.write_text("   \n")
    cfg = {"method": "agent", "agent_token_path": str(sink)}
    assert kind_of(resolve_token, cfg, HOST) is ErrorKind.NOT_CONFIGURED


# -- universal auth ---------------------------------------------------------


@responses.activate
def test_universal_auth_from_env_vars(monkeypatch):
    monkeypatch.setenv(CLIENT_ID_ENV, "cid")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "csecret")
    responses.post(LOGIN_URL, json={"accessToken": "ua-token"}, status=200)

    token = resolve_token({"method": "universal-auth"}, HOST)

    assert token == "ua-token"
    assert len(responses.calls) == 1
    body = responses.calls[0].request.body
    assert b"cid" in body and b"csecret" in body


@responses.activate
def test_universal_auth_from_files(monkeypatch, tmp_path):
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(CLIENT_SECRET_ENV, raising=False)
    (tmp_path / "id").write_text("file-cid\n")
    (tmp_path / "secret").write_text("file-csecret\n")
    responses.post(LOGIN_URL, json={"accessToken": "ua-token-2"}, status=200)

    cfg = {
        "method": "universal-auth",
        "client_id_path": str(tmp_path / "id"),
        "client_secret_path": str(tmp_path / "secret"),
    }
    assert resolve_token(cfg, HOST) == "ua-token-2"


def test_universal_auth_missing_credentials(monkeypatch):
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(CLIENT_SECRET_ENV, raising=False)
    kind = kind_of(resolve_token, {"method": "universal-auth"}, HOST)
    assert kind is ErrorKind.NOT_CONFIGURED


@responses.activate
@pytest.mark.parametrize("status", [400, 401, 403])
def test_universal_auth_rejected_is_auth_failed(monkeypatch, status):
    monkeypatch.setenv(CLIENT_ID_ENV, "cid")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "bad")
    responses.post(LOGIN_URL, json={"message": "nope"}, status=status)
    kind = kind_of(resolve_token, {"method": "universal-auth"}, HOST)
    assert kind is ErrorKind.AUTH_FAILED


@responses.activate
def test_universal_auth_network_failure(monkeypatch):
    monkeypatch.setenv(CLIENT_ID_ENV, "cid")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "csecret")
    import requests

    responses.post(LOGIN_URL, body=requests.ConnectionError("refused"))
    kind = kind_of(resolve_token, {"method": "universal-auth"}, HOST)
    assert kind is ErrorKind.NETWORK


@responses.activate
@pytest.mark.parametrize("payload", [{"unexpected": True}, [1, 2], "a string", None])
def test_universal_auth_malformed_response(monkeypatch, payload):
    monkeypatch.setenv(CLIENT_ID_ENV, "cid")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "csecret")
    responses.post(LOGIN_URL, json=payload, status=200)
    kind = kind_of(resolve_token, {"method": "universal-auth"}, HOST)
    assert kind is ErrorKind.INTERNAL


def test_credentials_never_appear_in_errors(monkeypatch):
    monkeypatch.setenv(CLIENT_ID_ENV, "topsecret-cid")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "topsecret-cs")
    with responses.RequestsMock() as mock:
        mock.post(LOGIN_URL, json={}, status=401)
        with pytest.raises(InfisicalError) as exc:
            resolve_token({"method": "universal-auth"}, HOST)
    assert "topsecret" not in str(exc.value)


# -- static token + dispatch -------------------------------------------------


def test_static_token(monkeypatch):
    monkeypatch.setenv(STATIC_TOKEN_ENV, "static-tok")
    assert resolve_token({"method": "token"}, HOST) == "static-tok"


def test_static_token_missing(monkeypatch):
    monkeypatch.delenv(STATIC_TOKEN_ENV, raising=False)
    assert kind_of(resolve_token, {"method": "token"}, HOST) is ErrorKind.NOT_CONFIGURED


def test_unknown_method(monkeypatch):
    assert kind_of(resolve_token, {"method": "voodoo"}, HOST) is ErrorKind.NOT_CONFIGURED


def test_non_dict_auth_section_falls_back_to_agent_default(monkeypatch, tmp_path):
    # Malformed auth section: default agent path won't exist under a fake HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert kind_of(resolve_token, "not-a-dict", HOST) is ErrorKind.NOT_CONFIGURED
