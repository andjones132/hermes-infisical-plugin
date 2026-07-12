from pathlib import Path

import pytest
import requests
import responses

from agent.secret_sources.base import SECRET_SOURCE_API_VERSION, ErrorKind

from infisical_plugin.sources import InfisicalSource

HOST = "https://infisical.example.com"
PROJECTS = {
    "projects": [
        {"id": "id-1", "name": "My Project", "slug": "my-project", "orgId": "org-1"}
    ]
}


@pytest.fixture
def base_cfg(tmp_path):
    sink = tmp_path / "agent-token"
    sink.write_text("tok-123")
    return {
        "enabled": True,
        "host_url": HOST,
        "auth": {"method": "agent", "agent_token_path": str(sink)},
        "env": {},
    }


def fetch(cfg, tmp_path=Path("/tmp")):
    return InfisicalSource().fetch(cfg, tmp_path)


def mock_secret(key, value, path="/"):
    responses.get(
        f"{HOST}/api/v4/secrets/{key}",
        json={"secret": {"secretKey": key, "secretValue": value}},
    )


# -- identity & hooks (task #17) ---------------------------------------------


def test_identity_attributes():
    src = InfisicalSource()
    assert src.name == "infisical"
    assert src.label == "Infisical"
    assert src.shape == "mapped"
    assert src.scheme == "inf"
    assert src.api_version == SECRET_SOURCE_API_VERSION


def test_override_existing_defaults_true():
    src = InfisicalSource()
    assert src.override_existing({}) is True
    assert src.override_existing({"override_existing": False}) is False
    assert src.override_existing("garbage") is False


def test_protected_env_vars():
    assert InfisicalSource().protected_env_vars({}) == frozenset(
        {"INFISICAL_TOKEN", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"}
    )


def test_config_schema_covers_spec_keys():
    schema = InfisicalSource().config_schema()
    for key in ("enabled", "env", "host_url", "organization", "auth",
                "timeout_seconds", "override_existing"):
        assert key in schema, key
        assert schema[key].get("description")


# -- mapped fetch (task #18) ---------------------------------------------------


@responses.activate
def test_happy_path_two_refs(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("OPENAI_API_KEY", "sk-1")
    mock_secret("ANTHROPIC_API_KEY", "sk-2")
    base_cfg["env"] = {
        "OPENAI_API_KEY": "inf://my-project/prod/backend/OPENAI_API_KEY",
        "ANTHROPIC_API_KEY": "inf://my-project/prod/ANTHROPIC_API_KEY",
    }

    result = fetch(base_cfg)

    assert result.ok, result.error
    assert result.secrets == {"OPENAI_API_KEY": "sk-1", "ANTHROPIC_API_KEY": "sk-2"}
    assert result.warnings == []


@responses.activate
def test_project_by_display_name(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("K", "v")
    base_cfg["env"] = {"K": "inf://My%20Project/prod/K"}
    result = fetch(base_cfg)
    assert result.secrets == {"K": "v"}


@responses.activate
def test_invalid_ref_warns_but_others_fetch(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("GOOD", "v")
    base_cfg["env"] = {
        "GOOD": "inf://my-project/prod/GOOD",
        "BAD": "inf://only-a-project",
        "not a name!": "inf://my-project/prod/X",
    }

    result = fetch(base_cfg)

    assert result.ok
    assert result.secrets == {"GOOD": "v"}
    assert len(result.warnings) == 2
    assert any("BAD" in w and "ref_invalid" in w for w in result.warnings)
    assert any("not a name!" in w for w in result.warnings)


def test_empty_env_map_is_not_configured(base_cfg):
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NOT_CONFIGURED
    assert "env: map is empty" in result.error
    assert not result.secrets


def test_env_map_not_a_dict_is_promoted_to_error(base_cfg):
    base_cfg["env"] = "not-a-dict"
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NOT_CONFIGURED
    assert not result.secrets


def test_all_refs_invalid_is_promoted_to_ref_invalid(base_cfg):
    base_cfg["env"] = {"BAD": "inf://only-a-project"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.REF_INVALID
    assert "BAD" in result.error  # names the offending env var
    assert not result.secrets


@responses.activate
def test_single_missing_secret_is_promoted_to_empty_value(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(f"{HOST}/api/v4/secrets/MISSING", json={}, status=404)
    base_cfg["env"] = {"MISSING": "inf://my-project/prod/MISSING"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.EMPTY_VALUE
    assert not result.secrets


@responses.activate
def test_resource_403_is_per_ref_warning_not_whole_fetch_abort(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("ALLOWED", "v")
    responses.get(f"{HOST}/api/v4/secrets/FORBIDDEN", body="forbidden", status=403)
    base_cfg["env"] = {
        "ALLOWED": "inf://my-project/prod/ALLOWED",
        "FORBIDDEN": "inf://my-project/prod/FORBIDDEN",
    }

    result = fetch(base_cfg)

    assert result.ok  # least-privilege denial on one path never sinks the rest
    assert result.secrets == {"ALLOWED": "v"}
    assert any("auth_failed" in w for w in result.warnings)


@responses.activate
def test_missing_secret_is_per_ref_warning(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(f"{HOST}/api/v4/secrets/MISSING", json={}, status=404)
    mock_secret("GOOD", "v")
    base_cfg["env"] = {
        "GOOD": "inf://my-project/prod/GOOD",
        "MISSING": "inf://my-project/prod/MISSING",
    }

    result = fetch(base_cfg)

    assert result.ok
    assert result.secrets == {"GOOD": "v"}
    assert any("empty_value" in w for w in result.warnings)


@responses.activate
def test_auth_rejection_fails_whole_fetch_and_clears_secrets(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("A", "va")
    responses.get(f"{HOST}/api/v4/secrets/B", body="unauthorized", status=401)
    base_cfg["env"] = {
        "A": "inf://my-project/prod/A",
        "B": "inf://my-project/prod/B",
    }

    result = fetch(base_cfg)

    assert result.error_kind is ErrorKind.AUTH_FAILED
    assert not result.secrets  # non-ok results must not carry secrets


@responses.activate
def test_network_failure(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", body=requests.ConnectionError("down"))
    base_cfg["env"] = {"K": "inf://my-project/prod/K"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NETWORK


def test_agent_sink_missing_is_not_configured(base_cfg, tmp_path):
    base_cfg["auth"]["agent_token_path"] = str(tmp_path / "nope")
    base_cfg["env"] = {"K": "inf://my-project/prod/K"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NOT_CONFIGURED
    assert str(tmp_path / "nope") in result.error


@responses.activate
def test_organization_mismatch(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    base_cfg["organization"] = "org-OTHER"
    base_cfg["env"] = {"K": "inf://my-project/prod/K"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NOT_CONFIGURED
    assert "org-OTHER" in result.error


@responses.activate
def test_organization_match_passes(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    mock_secret("K", "v")
    base_cfg["organization"] = "org-1"
    base_cfg["env"] = {"K": "inf://my-project/prod/K"}
    assert fetch(base_cfg).secrets == {"K": "v"}


@responses.activate
def test_organization_enforced_even_for_project_uuid_refs(base_cfg):
    """A raw project UUID must not bypass the configured org fence."""
    uuid_ref = "3f6c1d2e-0000-4a4a-9b9b-123456789abc"
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": uuid_ref, "name": "P", "slug": "p",
                            "orgId": "org-1"}]},
    )
    base_cfg["organization"] = "org-OTHER"
    base_cfg["env"] = {"K": f"inf://{uuid_ref}/prod/K"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.NOT_CONFIGURED
    assert "org-OTHER" in result.error


def test_fetch_never_raises_on_garbage_configs(tmp_path):
    src = InfisicalSource()
    for cfg in ({}, {"enabled": True}, {"enabled": True, "env": 42},
                {"enabled": True, "auth": "junk"}, None):
        result = src.fetch(cfg if isinstance(cfg, dict) else {}, tmp_path)
        assert result is not None
