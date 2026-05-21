from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

import config

Base = declarative_base()


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return

    db_path = Path(url.database)
    parent = db_path.parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_dir(config.DATABASE_URL)
engine = create_async_engine(config.DATABASE_URL, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    from database import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
