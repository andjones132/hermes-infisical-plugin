from pathlib import Path

import pytest
import responses

from agent.secret_sources.base import SECRET_SOURCE_API_VERSION, ErrorKind

from infisical_plugin.sources import InfisicalBulkSource

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
        "project": "my-project",
        "environment": "prod",
        "secret_path": "/backend",
    }


def fetch(cfg):
    return InfisicalBulkSource().fetch(cfg, Path("/tmp"))


def test_identity_attributes():
    src = InfisicalBulkSource()
    assert src.name == "infisical_bulk"
    assert src.shape == "bulk"
    assert src.scheme is None
    assert src.api_version == SECRET_SOURCE_API_VERSION
    assert src.override_existing({}) is False  # bulk yields by default


def test_config_schema_covers_spec_keys():
    schema = InfisicalBulkSource().config_schema()
    for key in ("enabled", "project", "environment", "secret_path", "recursive",
                "include_imports", "host_url", "auth", "organization",
                "timeout_seconds", "override_existing"):
        assert key in schema, key


@responses.activate
def test_injects_whole_folder(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={
            "secrets": [
                {"secretKey": "A", "secretValue": "1"},
                {"secretKey": "B", "secretValue": "2"},
                {"secretKey": "C", "secretValue": "3"},
            ],
            "imports": [],
        },
        match=[
            responses.matchers.query_param_matcher(
                {
                    "projectId": "id-1",
                    "environment": "prod",
                    "secretPath": "/backend",
                    "recursive": "false",
                    "includeImports": "true",
                    "viewSecretValue": "true",
                    "expandSecretReferences": "true",
                }
            )
        ],
    )

    result = fetch(base_cfg)

    assert result.ok, result.error
    assert result.secrets == {"A": "1", "B": "2", "C": "3"}


@responses.activate
def test_direct_secrets_win_over_imports(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={
            "secrets": [{"secretKey": "A", "secretValue": "direct"}],
            "imports": [
                {"secrets": [{"secretKey": "A", "secretValue": "imported"},
                             {"secretKey": "B", "secretValue": "b"}]}
            ],
        },
    )
    result = fetch(base_cfg)
    assert result.secrets == {"A": "direct", "B": "b"}


@responses.activate
def test_invalid_key_names_and_empty_values_skipped_with_warnings(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={
            "secrets": [
                {"secretKey": "GOOD", "secretValue": "v"},
                {"secretKey": "1BAD-NAME", "secretValue": "x"},
                {"secretKey": "EMPTY", "secretValue": ""},
            ],
            "imports": [],
        },
    )

    result = fetch(base_cfg)

    assert result.secrets == {"GOOD": "v"}
    assert any("1BAD-NAME" in w for w in result.warnings)
    assert any("EMPTY" in w for w in result.warnings)


def test_missing_project_or_environment_is_not_configured(base_cfg):
    for missing in ("project", "environment"):
        cfg = dict(base_cfg)
        cfg.pop(missing)
        result = fetch(cfg)
        assert result.error_kind is ErrorKind.NOT_CONFIGURED
        assert "project" in result.error and "environment" in result.error


@responses.activate
def test_recursive_flag_passed_through(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={"secrets": [], "imports": []},
        match=[
            responses.matchers.query_param_matcher(
                {"recursive": "true"}, strict_match=False
            )
        ],
    )
    base_cfg["recursive"] = True
    assert fetch(base_cfg).ok


@responses.activate
def test_auth_rejection_sets_error(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", body="unauthorized", status=401)
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.AUTH_FAILED
    assert not result.secrets


def test_fetch_never_raises_on_garbage_configs(tmp_path):
    src = InfisicalBulkSource()
    for cfg in ({}, {"enabled": True}, {"enabled": True, "project": 42},
                {"enabled": True, "auth": []}, None):
        result = src.fetch(cfg if isinstance(cfg, dict) else {}, tmp_path)
        assert result is not None
