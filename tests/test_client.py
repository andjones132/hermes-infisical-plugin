import time

import pytest
import requests
import responses

from agent.secret_sources.base import ErrorKind

from infisical_plugin.client import (
    Deadline,
    InfisicalClient,
    Project,
    _reset_project_cache_for_tests,
)
from infisical_plugin.errors import InfisicalError

HOST = "https://infisical.example.com"
PROJECT_ID = "3f6c1d2e-0000-4a4a-9b9b-123456789abc"


def make_client(budget: float = 60.0) -> InfisicalClient:
    return InfisicalClient(HOST, "test-token", Deadline(budget_seconds=budget))


def raises_kind(fn, *args, **kwargs) -> InfisicalError:
    with pytest.raises(InfisicalError) as exc:
        result = fn(*args, **kwargs)
        if hasattr(result, "__next__"):  # force generators
            list(result)
    return exc.value


# -- Deadline ----------------------------------------------------------------


def test_deadline_remaining_decreases():
    deadline = Deadline(budget_seconds=10)
    first = deadline.remaining()
    time.sleep(0.01)
    assert deadline.remaining() < first


def test_deadline_clamps_per_call_timeout():
    deadline = Deadline(budget_seconds=5)
    assert deadline.per_call_timeout(per_call=30) <= 5


def test_deadline_uses_smaller_per_call_value():
    deadline = Deadline(budget_seconds=100)
    assert deadline.per_call_timeout(per_call=30) <= 30


def test_exhausted_deadline_raises_timeout():
    deadline = Deadline(budget_seconds=-1)
    with pytest.raises(InfisicalError) as exc:
        deadline.per_call_timeout()
    assert exc.value.kind is ErrorKind.TIMEOUT


# -- transport / status mapping ------------------------------------------------


@responses.activate
def test_bearer_token_sent():
    responses.get(f"{HOST}/api/v1/projects", json={"projects": []})
    client = make_client()
    raises_kind(client.resolve_project, "some-name")  # no match, but call happened
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
@pytest.mark.parametrize(
    "status,body,kind",
    [
        (401, "unauthorized", ErrorKind.AUTH_FAILED),
        (401, "token expired", ErrorKind.AUTH_EXPIRED),
        (403, "forbidden", ErrorKind.AUTH_FAILED),
        (429, "slow down", ErrorKind.NETWORK),
        (500, "boom", ErrorKind.INTERNAL),
        (502, "bad gateway", ErrorKind.NETWORK),
        (503, "unavailable", ErrorKind.NETWORK),
    ],
)
def test_status_mapping(status, body, kind):
    responses.get(f"{HOST}/api/v4/secrets/K", body=body, status=status)
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "K")
    assert err.kind is kind


@responses.activate
def test_connection_error_is_network():
    responses.get(
        f"{HOST}/api/v4/secrets/K", body=requests.ConnectionError("refused")
    )
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "K")
    assert err.kind is ErrorKind.NETWORK


@responses.activate
def test_request_timeout_is_timeout():
    responses.get(f"{HOST}/api/v4/secrets/K", body=requests.Timeout("slow"))
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "K")
    assert err.kind is ErrorKind.TIMEOUT


# -- get_secret ---------------------------------------------------------------


@responses.activate
def test_get_secret_happy_path():
    responses.get(
        f"{HOST}/api/v4/secrets/OPENAI_API_KEY",
        json={"secret": {"secretKey": "OPENAI_API_KEY", "secretValue": "sk-123"}},
        match=[
            responses.matchers.query_param_matcher(
                {
                    "projectId": PROJECT_ID,
                    "environment": "prod",
                    "secretPath": "/backend",
                    "viewSecretValue": "true",
                    "expandSecretReferences": "true",
                    "includeImports": "true",
                }
            )
        ],
    )
    value = make_client().get_secret(PROJECT_ID, "prod", "/backend", "OPENAI_API_KEY")
    assert value == "sk-123"


@responses.activate
def test_get_secret_404_is_empty_value():
    responses.get(f"{HOST}/api/v4/secrets/MISSING", json={}, status=404)
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "MISSING")
    assert err.kind is ErrorKind.EMPTY_VALUE


@responses.activate
def test_get_secret_403_is_resource_scoped():
    responses.get(f"{HOST}/api/v4/secrets/K", body="forbidden", status=403)
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "K")
    assert err.kind is ErrorKind.AUTH_FAILED
    assert err.resource_scoped is True


@responses.activate
def test_get_secret_key_is_url_quoted():
    responses.get(
        f"{HOST}/api/v4/secrets/a%2Fb%3Fx%23y",
        json={"secret": {"secretValue": "v"}},
    )
    value = make_client().get_secret(PROJECT_ID, "prod", "/", "a/b?x#y")
    assert value == "v"
    # The raw special chars never reach the URL unencoded.
    assert "a%2Fb%3Fx%23y" in responses.calls[0].request.url


@responses.activate
def test_get_secret_empty_value_rejected():
    responses.get(
        f"{HOST}/api/v4/secrets/EMPTY", json={"secret": {"secretValue": ""}}
    )
    err = raises_kind(make_client().get_secret, PROJECT_ID, "prod", "/", "EMPTY")
    assert err.kind is ErrorKind.EMPTY_VALUE


# -- list_secrets ---------------------------------------------------------------


@responses.activate
def test_list_secrets_yields_imports_first_then_direct():
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={
            "secrets": [{"secretKey": "A", "secretValue": "direct"}],
            "imports": [
                {
                    "secretPath": "/shared",
                    "secrets": [
                        {"secretKey": "A", "secretValue": "imported"},
                        {"secretKey": "B", "secretValue": "b"},
                    ],
                }
            ],
        },
    )
    out = list(make_client().list_secrets(PROJECT_ID, "prod", "/"))
    keys = [s["secretKey"] for s in out]
    assert keys == ["A", "B", "A"]
    merged = {s["secretKey"]: s["secretValue"] for s in out}
    assert merged == {"A": "direct", "B": "b"}  # direct wins over import


@responses.activate
def test_list_secrets_lazy_generator():
    client = make_client()
    gen = client.list_secrets(PROJECT_ID, "prod", "/")
    assert len(responses.calls) == 0  # nothing fetched until iteration
    responses.get(f"{HOST}/api/v4/secrets", json={"secrets": [], "imports": []})
    assert list(gen) == []


@responses.activate
def test_list_secrets_bad_shape_is_internal():
    responses.get(f"{HOST}/api/v4/secrets", json={"nope": True})
    err = raises_kind(
        lambda: list(make_client().list_secrets(PROJECT_ID, "prod", "/"))
    )
    assert err.kind is ErrorKind.INTERNAL


# -- resolve_project ---------------------------------------------------------------


PROJECTS = {
    "projects": [
        {"id": "id-1", "name": "My Project", "slug": "my-project", "orgId": "org-1"},
        {"id": "id-2", "name": "Other", "slug": "other", "orgId": "org-1"},
        {"id": "id-3", "name": "Dupe", "slug": "dupe-a", "orgId": "org-1"},
        {"id": "id-4", "name": "Dupe", "slug": "dupe-b", "orgId": "org-1"},
    ]
}


@responses.activate
def test_resolve_project_uuid_fast_path_skips_http():
    project = make_client().resolve_project(PROJECT_ID)
    assert project.id == PROJECT_ID
    assert len(responses.calls) == 0


@responses.activate
def test_resolve_project_by_slug_and_by_name():
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    client = make_client()
    assert client.resolve_project("my-project") == Project(
        id="id-1", slug="my-project", name="My Project", org_id="org-1"
    )
    assert client.resolve_project("Other").id == "id-2"


@responses.activate
def test_resolve_project_cache_hit_skips_http():
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    client = make_client()
    client.resolve_project("my-project")
    client.resolve_project("my-project")
    assert len(responses.calls) == 1


@responses.activate
def test_resolve_project_ambiguous_name():
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    err = raises_kind(make_client().resolve_project, "Dupe")
    assert err.kind is ErrorKind.REF_INVALID
    assert "ambiguous" in str(err)


@responses.activate
def test_resolve_project_uuid_with_require_full_uses_listing():
    uuid = "3f6c1d2e-0000-4a4a-9b9b-123456789abc"
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": uuid, "name": "N", "slug": "s", "orgId": "o"}]},
    )
    project = make_client().resolve_project(uuid, require_full=True)
    assert project == Project(id=uuid, slug="s", name="N", org_id="o")
    assert len(responses.calls) == 1


@responses.activate
def test_project_cache_is_partitioned_by_identity():
    """Resolutions under one token must not be served to another identity."""
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    client_a = InfisicalClient(HOST, "token-A", Deadline(budget_seconds=60))
    client_b = InfisicalClient(HOST, "token-B", Deadline(budget_seconds=60))
    client_a.resolve_project("my-project")
    client_b.resolve_project("my-project")
    assert len(responses.calls) == 2  # no cross-identity cache hit


@responses.activate
def test_resolve_project_not_found_lists_slugs():
    responses.get(f"{HOST}/api/v1/projects", json=PROJECTS)
    err = raises_kind(make_client().resolve_project, "ghost")
    assert err.kind is ErrorKind.REF_INVALID
    assert "my-project" in str(err)


# -- dynamic leases ---------------------------------------------------------------


@responses.activate
def test_create_dynamic_lease():
    responses.post(
        f"{HOST}/api/v1/dynamic-secrets/leases",
        json={
            "lease": {"id": "lease-1", "expireAt": "2026-07-11T12:00:00Z"},
            "data": {"username": "u1", "password": "p1"},
        },
        match=[
            responses.matchers.json_params_matcher(
                {
                    "dynamicSecretName": "pg-creds",
                    "projectSlug": "my-project",
                    "environmentSlug": "prod",
                    "path": "/db",
                    "ttl": "8h",
                }
            )
        ],
    )
    data = make_client().create_dynamic_lease(
        "my-project", "prod", "/db", "pg-creds", ttl="8h"
    )
    assert data == {"username": "u1", "password": "p1"}


@responses.activate
def test_create_dynamic_lease_omits_ttl_when_none():
    responses.post(
        f"{HOST}/api/v1/dynamic-secrets/leases",
        json={"lease": {}, "data": {"password": "p"}},
        match=[
            responses.matchers.json_params_matcher(
                {
                    "dynamicSecretName": "pg",
                    "projectSlug": "s",
                    "environmentSlug": "e",
                    "path": "/",
                }
            )
        ],
    )
    assert make_client().create_dynamic_lease("s", "e", "/", "pg") == {"password": "p"}


@responses.activate
def test_create_dynamic_lease_404_is_empty_value():
    responses.post(f"{HOST}/api/v1/dynamic-secrets/leases", json={}, status=404)
    err = raises_kind(
        make_client().create_dynamic_lease, "s", "e", "/", "ghost"
    )
    assert err.kind is ErrorKind.EMPTY_VALUE


@responses.activate
def test_create_dynamic_lease_empty_data_rejected():
    responses.post(
        f"{HOST}/api/v1/dynamic-secrets/leases", json={"lease": {}, "data": {}}
    )
    err = raises_kind(make_client().create_dynamic_lease, "s", "e", "/", "pg")
    assert err.kind is ErrorKind.EMPTY_VALUE
