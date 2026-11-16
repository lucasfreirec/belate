from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        # Give up after 5 seconds rather than hang a request or a health check.
        connect_args={"connect_timeout": 5},
    )


def new_session() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()
