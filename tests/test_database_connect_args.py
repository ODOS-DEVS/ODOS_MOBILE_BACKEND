"""TLS must be forced onto managed databases and never onto internal ones.

Getting this backwards is not a subtle bug: `sslmode=require` against a plain
container Postgres fails every single connection, so the app boots, answers
/health, and returns 500 from every route that touches the database.
"""

import pytest

from app.core.database import _build_connect_args


def _url(host: str) -> str:
    return f"postgresql+psycopg://odos:pa/ss@{host}:5432/odos_mobile"


@pytest.mark.parametrize(
    "host",
    [
        "d4nwvegnlxnqgvkopgu50jhf",  # the real Coolify service name in production
        "odos-db",
        "postgres",
        "db",
        "localhost",
        "127.0.0.1",
        "10.0.1.14",
        "172.18.0.5",
        "192.168.1.20",
        "::1",
    ],
)
def test_internal_hosts_get_no_forced_tls(host: str) -> None:
    assert _build_connect_args(_url(host)) == {}


@pytest.mark.parametrize(
    "host",
    [
        "ep-cool-darkness-a5b1c2d3.us-east-2.aws.neon.tech",
        "db.abcdefgh.supabase.co",
        "odos.cluster-xyz.eu-west-1.rds.amazonaws.com",
        "169.58.197.157",  # a real routable address, not a documentation range
    ],
)
def test_public_hosts_get_tls_and_keepalives(host: str) -> None:
    args = _build_connect_args(_url(host))
    assert args["sslmode"] == "require"
    assert args["keepalives"] == 1


def test_explicit_sslmode_in_the_url_is_never_downgraded() -> None:
    url = _url("ep-x.us-east-2.aws.neon.tech") + "?sslmode=verify-full"
    assert "sslmode" not in _build_connect_args(url)


def test_password_containing_a_slash_does_not_confuse_host_parsing() -> None:
    # urlsplit reports the *username* as the host for this URL; make_url does not.
    url = "postgresql+psycopg://odos:ab/cd?ef@d4nwvegnlxnqgvkopgu50jhf:5432/odos_mobile"
    assert _build_connect_args(url) == {}


def test_unparseable_url_is_left_alone() -> None:
    assert _build_connect_args("not a url at all") == {}
