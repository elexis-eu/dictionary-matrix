import logging
import os
import re
from contextlib import contextmanager
from functools import lru_cache
from typing import AsyncGenerator, ContextManager

import pymongo.collection
import pymongo.database
import pymongo.errors
from filelock import FileLock
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from .settings import settings

log = logging.getLogger(__name__)


@lru_cache(1)
def _db_client() -> MongoClient:
    return AsyncIOMotorClient(settings.MONGODB_CONNECTION_STRING)


@lru_cache(1)
def _db_client_sync() -> MongoClient:
    return MongoClient(settings.MONGODB_CONNECTION_STRING)


def reset_db_client():
    """
    Motor/PyMongo doesn't support client sharing over threads/forked processes.
    Call this in any thread/forked process before get_db().
    """
    _db_client.cache_clear()
    _db_client_sync.cache_clear()


class _DbType(pymongo.database.Database):
    dicts: pymongo.collection.Collection
    entry: pymongo.collection.Collection
    import_jobs: pymongo.collection.Collection
    linking_jobs: pymongo.collection.Collection


_collection_names = [k for k, v in _DbType.__annotations__.items()
                     if v is pymongo.collection.Collection]


async def get_db() -> AsyncGenerator[_DbType, None]:
    """FastAPI uses this as an AsyncContextManager."""
    async with await _db_client().start_session() as session:
        async with session.start_transaction():
            db = session.client[settings.MONGODB_DATABASE]
            yield db


@contextmanager  # type: ignore
def get_db_sync() -> ContextManager[_DbType]:
    """
    Synchronous database client for use in subprocess/commands
    w/o event loop frivolity. Used as a context manager.
    """
    with _db_client_sync().start_session() as session:
        with session.start_transaction():
            db = session.client[settings.MONGODB_DATABASE]
            yield db


def safe_path(part):
    return re.sub(r'[^\w.-]', '_', part)


def dispatch_migration():
    """
    In-house migrations dispatcher.
    Hopefully, it is feature-complete and will never need amendments. 🤞
    """
    lock_file = os.path.join(settings.UPLOAD_PATH,
                             safe_path(f'{settings.MONGODB_CONNECTION_STRING}'
                                       f'-{settings.MONGODB_DATABASE}.lock'))
    try:
        with FileLock(lock_file), \
                get_db_sync() as db:  # type: _DbType
            if not db.list_collection_names():
                return _migration_v0(db)
            ...
            assert sorted(db.list_collection_names()) == sorted(_collection_names), \
                "Db collections don't match expectations." \
                "Prolly a residue db or missing a migration."
    finally:
        # Close/clear the sync client not to leave
        # the connection needlessly open
        _db_client_sync().close()
        _db_client_sync.cache_clear()


def _migration_v0(db: _DbType):
    log.info('Init database ...')

    for collection in _collection_names:
        try:
            db.create_collection(collection)
            # TODO add schema validation; clean schema (bsonType, integer, $ref)
            # db.create_collection(collection, {'validator': {'$jsonSchema': schema}})
        except pymongo.errors.CollectionInvalid as exc:
            assert 'already exists' in str(exc)

    # Create indexes
    db.dicts.create_index('api_key')
    db.entry.create_index([('_dict_id', 1), ('lemma', 1)])

    # TODO create views?

    log.info('Created collections: %s', ', '.join(db.list_collection_names()))
