from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        _ensure_sqlite_parent_dir(database_url)
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    kwargs = {"poolclass": StaticPool} if database_url == "sqlite:///:memory:" else {}
    engine = create_engine(database_url, pool_pre_ping=True, future=True, connect_args=connect_args, **kwargs)
    if is_sqlite and database_url != "sqlite:///:memory:":
        _configure_sqlite(engine)
    return sessionmaker(engine, expire_on_commit=False, autoflush=False, future=True)


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if not database or database == ":memory:":
        return
    parent = Path(database).expanduser().parent
    if str(parent) not in {"", "."}:
        parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def migrate(session_factory: sessionmaker[Session]) -> None:
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    _migrate_existing_schema(engine)


def _migrate_existing_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "audit_requests" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("audit_requests")}
    if "prompt_omitted" not in columns:
        default = "false" if engine.dialect.name == "postgresql" else "0"
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE audit_requests ADD COLUMN prompt_omitted BOOLEAN NOT NULL DEFAULT {default}"))


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
