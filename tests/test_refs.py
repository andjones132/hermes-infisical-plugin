import pytest

from agent.secret_sources.base import ErrorKind

from infisical_plugin.errors import InfisicalError
from infisical_plugin.refs import Ref, parse_ref


def test_minimal_ref_root_path():
    ref = parse_ref("inf://my-project/prod/OPENAI_API_KEY")
    assert ref == Ref(
        project="my-project",
        environment="prod",
        secret_path="/",
        key="OPENAI_API_KEY",
    )


def test_nested_folders():
    ref = parse_ref("inf://proj/staging/backend/payments/STRIPE_KEY")
    assert ref.secret_path == "/backend/payments"
    assert ref.key == "STRIPE_KEY"


def test_percent_encoded_project_name():
    ref = parse_ref("inf://My%20Project/dev/KEY")
    assert ref.project == "My Project"


def test_whitespace_is_stripped():
    ref = parse_ref("  inf://p/e/K  ")
    assert ref.key == "K"


def test_dynamic_ref_with_field_and_ttl():
    ref = parse_ref("inf://p/prod/db/pg-creds?type=dynamic&field=password&ttl=8h")
    assert ref.dynamic is True
    assert ref.field == "password"
    assert ref.ttl == "8h"
    assert ref.secret_path == "/db"
    assert ref.key == "pg-creds"


def test_dynamic_without_field_or_ttl():
    ref = parse_ref("inf://p/prod/pg?type=dynamic")
    assert ref.dynamic is True
    assert ref.field is None
    assert ref.ttl is None


@pytest.mark.parametrize(
    "bad",
    [
        "op://vault/item/field",          # wrong scheme
        "inf://only-a-project",            # too few segments
        "inf://p/e",                       # missing key
        "inf://p//K",                      # empty segment
        "inf://p/e/K?bogus=1",             # unknown option
        "inf://p/e/K?type=static",         # invalid type value
        "inf://p/e/K?field=x",             # field without dynamic
        "inf://p/e/K?ttl=1h",              # ttl without dynamic
        "inf://p/e/K?type=dynamic&type=dynamic",  # repeated option
        "inf://p/e/K?type=",               # empty option value
        12345,                             # not a string
    ],
)
def test_invalid_refs_raise_ref_invalid(bad):
    with pytest.raises(InfisicalError) as exc:
        parse_ref(bad)
    assert exc.value.kind is ErrorKind.REF_INVALID
    assert "expected inf://" in str(exc.value)
