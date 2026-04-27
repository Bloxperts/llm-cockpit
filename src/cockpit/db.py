"""SQLAlchemy engine + alembic glue.

A single sync engine per process is enough for the cockpit's scale (5 users,
SQLite). Async would buy us nothing here and complicate the test fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(db_url: str) -> Engine:
    engine = create_engine(db_url, future=True)
    # Enable WAL journal mode for SQLite so background tasks (GpuSampler,
    # ModelStateSampler) can INSERT concurrently with request-handler reads.
    # WAL allows one writer + many concurrent readers without locking.
    if db_url.startswith("sqlite"):
        from sqlalchemy import event as sa_event

        @sa_event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _connection_record):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA busy_timeout=5000")  # 5 s retry on lock

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config_for(db_url: str) -> AlembicConfig:
    """Build an Alembic Config pointed at the bundled `migrations/` package.

    We don't ship an `alembic.ini` on disk; everything is set programmatically
    so the wheel-installed package finds its scripts via `importlib.resources`.
    """
    migrations_dir = resources.files("cockpit").joinpath("migrations")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def upgrade_to_head(db_url: str) -> None:
    cfg = alembic_config_for(db_url)
    command.upgrade(cfg, "head")


def current_revision(db_url: str) -> str | None:
    """Return the current alembic revision in the DB, or None if uninitialised."""
    from alembic.runtime.migration import MigrationContext

    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    cfg = alembic_config_for("sqlite:///:memory:")
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


def ensure_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
