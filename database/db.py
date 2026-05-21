from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

import config

Base = declarative_base()

engine = create_async_engine(config.DATABASE_URL, future=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    from database import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
