from agent.secret_sources.base import ErrorKind

from infisical_plugin.errors import InfisicalError, format_error


def test_format_error_shape():
    assert (
        format_error(ErrorKind.AUTH_FAILED, "bad credentials")
        == "infisical: auth_failed: bad credentials"
    )


def test_infisical_error_carries_kind_and_message():
    err = InfisicalError(ErrorKind.NETWORK, "connection refused")
    assert err.kind is ErrorKind.NETWORK
    assert err.message == "connection refused"
    assert str(err) == "infisical: network: connection refused"


def test_every_kind_round_trips_through_formatter():
    for kind in ErrorKind:
        err = InfisicalError(kind, "detail")
        assert str(err).startswith(f"infisical: {kind.value}: ")
