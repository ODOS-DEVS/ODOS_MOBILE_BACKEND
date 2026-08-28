import ipaddress

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


# Bare hostnames that always mean "a database next to us", regardless of shape.
_LOCAL_HOSTNAMES = {"localhost", "postgres", "postgresql", "db", "database"}


def _is_internal_host(host: str) -> bool:
    """True when the host is reachable on a private network rather than the internet.

    This deliberately tests the *shape* of the host instead of matching a list of
    known names. Coolify, Docker Compose, Kubernetes and Swarm all address a
    sibling database by its service name -- ``d4nwvegnlxnqgvkopgu50jhf``,
    ``odos-db``, ``postgres-primary`` -- which is a single DNS label with no dot
    and is unguessable ahead of time. A managed provider (Neon, RDS, Supabase)
    is always a fully-qualified name. That structural difference is the reliable
    signal; an allowlist of names is not, and getting it wrong forces TLS onto a
    container Postgres that does not speak it, which fails every connection.
    """
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal, so judge by DNS shape: no dot means a single label,
        # which can only resolve on an internal network.
        return "." not in host
    return address.is_loopback or address.is_private or address.is_link_local


def _build_connect_args(url: str) -> dict[str, object]:
    """libpq options that keep a Neon (or any managed/pooled) connection healthy.

    Neon terminates idle connections at its proxy and sits behind the public
    internet, so two things matter that do not for a Postgres container on the
    same Docker network:

    * TLS is mandatory. Neon rejects a plaintext connection, and a URL pasted
      without ``?sslmode=`` is the usual cause of a deploy that cannot reach its
      own database. We only default it in when the URL does not already say
      something, so an explicit ``sslmode=verify-full`` is never downgraded.
    * A dropped connection should be noticed by the client rather than
      discovered as a hung query. TCP keepalives turn a silently severed link
      into a prompt error that ``pool_pre_ping`` can then recycle.

    Neither is applied to an internal host: keepalives are pointless over a
    private network, and forcing TLS breaks a plain container Postgres.
    """
    # SQLAlchemy's parser, not urllib's: passwords here are not percent-encoded
    # and a "/" or "?" inside one makes urlsplit truncate the netloc, so it
    # reports the *username* as the host and we would force TLS onto loopback.
    try:
        parsed = make_url(url)
    except Exception:
        return {}

    host = (parsed.host or "").lower().strip("[]")
    if not host or _is_internal_host(host):
        return {}

    connect_args: dict[str, object] = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    if "sslmode" not in parsed.query:
        connect_args["sslmode"] = "require"
    return connect_args


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    connect_args=_build_connect_args(settings.database_url),
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
