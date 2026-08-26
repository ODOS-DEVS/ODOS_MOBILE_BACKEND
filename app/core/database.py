from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}


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

    Neither is applied to a local host: keepalives are pointless over loopback,
    and forcing TLS would break a plain local Postgres.
    """
    # SQLAlchemy's parser, not urllib's: passwords here are not percent-encoded
    # and a "/" or "?" inside one makes urlsplit truncate the netloc, so it
    # reports the *username* as the host and we would force TLS onto loopback.
    try:
        parsed = make_url(url)
    except Exception:
        return {}

    host = (parsed.host or "").lower()
    if not host or host in _LOCAL_HOSTS:
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
