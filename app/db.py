import logging
import os
import re
from functools import lru_cache
from typing import AsyncGenerator

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


class get_db_sync:
    """
    Synchronous database client for use in subprocess/commands
    w/o event loop frivolity. Used as a context manager.
    """
    # Can use simple contextlib.contextmanager once this is fixed:
    # https://youtrack.jetbrains.com/issue/PY-36444
    def __enter__(self) -> _DbType:
        self._session = session = _db_client_sync().start_session()
        self._transaction = transaction = session.start_transaction()
        session.__enter__()
        transaction.__enter__()
        return session.client[settings.MONGODB_DATABASE]

    def __exit__(self, *args, **kwargs):
        self._transaction.__exit__(*args, **kwargs)
        self._session.__exit__(*args, **kwargs)


def safe_path(part):
    return re.sub(r'[^\w.-]', '_', part)


def dispatch_migration():
    """
    In-house migrations dispatcher.
    Hopefully, it is feature-complete and will never need amendments. 🤞
    """
    path = os.path.join(settings.UPLOAD_PATH,
                        safe_path(f'{settings.MONGODB_CONNECTION_STRING}'
                                  f'-{settings.MONGODB_DATABASE}'))
    lock_file = path + '.lock'
    pid_file = path + '.pid'

    with FileLock(lock_file):
        with open(pid_file, 'a+') as fd:
            fd.seek(0)
            pid = fd.read()
            if not pid:
                fd.seek(0)
                fd.write(str(os.getpid()))
                log.info(f'Process {os.getpid()} will migrate the DB')
    try:
        with open(pid_file, 'r') as fd:
            if int(fd.read()) != os.getpid():
                return
    except IOError:
        # If file no longer exists, we were not responsible for it
        return

    try:
        log.info(f'Process {os.getpid()} is migrating the DB')
        with get_db_sync() as db:
            _migration_v0(db)
            assert sorted(db.list_collection_names()) == sorted(_collection_names), \
                "Db collections don't match expectations." \
                "Prolly a residue db or missing a migration."
    finally:
        os.remove(pid_file)
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

        # Drop indexes
        db[collection].drop_indexes()

    # Create indexes
    for collection, index in [
        ('dicts', 'api_key'),
        ('entry', [('_dict_id', pymongo.ASCENDING),
                   ('lemma', pymongo.ASCENDING)]),
        ('entry', [('origin_id', pymongo.ASCENDING)]),
    ]:
        db[collection].create_index(index)

    # TODO create views?

    log.info('Created collections: %s', ', '.join(db.list_collection_names()))
