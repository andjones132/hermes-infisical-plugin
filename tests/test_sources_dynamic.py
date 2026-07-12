from pathlib import Path

import json

import pytest
import responses

from agent.secret_sources.base import ErrorKind

from infisical_plugin.sources import InfisicalSource

HOST = "https://infisical.example.com"
LEASES_URL = f"{HOST}/api/v1/dynamic-secrets/leases"
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


def fetch(cfg):
    return InfisicalSource().fetch(cfg, Path("/tmp"))


@responses.activate
def test_lease_created_with_ttl_and_field(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(
        LEASES_URL,
        json={"lease": {"id": "l1"}, "data": {"username": "u", "password": "p"}},
    )
    base_cfg["env"] = {
        "DB_PASSWORD": "inf://my-project/prod/db/pg?type=dynamic&field=password&ttl=8h"
    }

    result = fetch(base_cfg)

    assert result.ok, result.error
    assert result.secrets == {"DB_PASSWORD": "p"}
    body = json.loads(responses.calls[-1].request.body)
    assert body == {
        "dynamicSecretName": "pg",
        "projectSlug": "my-project",
        "environmentSlug": "prod",
        "path": "/db",
        "ttl": "8h",
    }


@responses.activate
def test_single_field_payload_needs_no_field_option(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(LEASES_URL, json={"lease": {}, "data": {"token": "t-1"}})
    base_cfg["env"] = {"API_TOKEN": "inf://my-project/prod/gen?type=dynamic"}
    assert fetch(base_cfg).secrets == {"API_TOKEN": "t-1"}


@responses.activate
def test_multi_field_without_field_option_lists_fields(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(
        LEASES_URL, json={"lease": {}, "data": {"username": "u", "password": "p"}}
    )
    base_cfg["env"] = {"X": "inf://my-project/prod/pg?type=dynamic"}

    result = fetch(base_cfg)

    # Sole binding failed -> promoted to a hard REF_INVALID error.
    assert result.error_kind is ErrorKind.REF_INVALID
    assert not result.secrets
    warning = result.warnings[0]
    assert "ref_invalid" in warning
    assert "password" in warning and "username" in warning


@responses.activate
def test_unknown_field_lists_available(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(LEASES_URL, json={"lease": {}, "data": {"password": "p"}})
    base_cfg["env"] = {"X": "inf://my-project/prod/pg?type=dynamic&field=nope"}

    result = fetch(base_cfg)

    assert any("no field 'nope'" in w and "password" in w for w in result.warnings)


@responses.activate
def test_dynamic_ref_with_project_uuid_resolves_slug_via_listing(base_cfg):
    uuid_ref = "3f6c1d2e-0000-4a4a-9b9b-123456789abc"
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": uuid_ref, "name": "P", "slug": "p-slug",
                            "orgId": "org-1"}]},
    )
    responses.post(LEASES_URL, json={"lease": {}, "data": {"token": "t"}})
    base_cfg["env"] = {"X": f"inf://{uuid_ref}/prod/pg?type=dynamic"}

    result = fetch(base_cfg)

    assert result.ok, result.error
    assert result.secrets == {"X": "t"}
    body = json.loads(responses.calls[-1].request.body)
    assert body["projectSlug"] == "p-slug"  # slug resolved from the ID


@responses.activate
def test_lease_failure_is_per_ref_warning(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(LEASES_URL, json={}, status=404)
    responses.get(
        f"{HOST}/api/v4/secrets/STATIC",
        json={"secret": {"secretValue": "s"}},
    )
    base_cfg["env"] = {
        "DYN": "inf://my-project/prod/ghost?type=dynamic",
        "STATIC": "inf://my-project/prod/STATIC",
    }

    result = fetch(base_cfg)

    assert result.ok
    assert result.secrets == {"STATIC": "s"}
    assert any("empty_value" in w for w in result.warnings)


@responses.activate
def test_dynamic_kind_error_maps_to_error_kind(base_cfg):
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.post(LEASES_URL, body="unauthorized", status=401)
    base_cfg["env"] = {"X": "inf://my-project/prod/pg?type=dynamic"}
    result = fetch(base_cfg)
    assert result.error_kind is ErrorKind.AUTH_FAILED
