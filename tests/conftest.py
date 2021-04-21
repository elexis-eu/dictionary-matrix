import asyncio
import logging
import os
import time
from http import HTTPStatus
from pathlib import Path
from typing import AsyncGenerator

import pytest
from asgi_lifespan import LifespanManager
from bson import ObjectId
from httpx import AsyncClient

os.environ['MONGODB_DATABASE'] = 'dictionary_matrix_tests'
logging.getLogger("asyncio").setLevel(logging.DEBUG)

if True:  # Avoids flake8 raising E402
    from app import app, settings
    from app.db import _db_client_sync, get_db_sync
    from app.rdf import file_to_obj


EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"
EXAMPLE_FILES = [
    'example.ttl',
    'example-tei.xml',
    'example.json',
]


async def verify_upload(client, id):
    for i in range(5):
        response = await client.get(f'/about/{id}')
        if response.status_code == HTTPStatus.OK:
            break
        time.sleep(.1)
    else:
        raise RuntimeError('Failed to load example')


@pytest.fixture(params=EXAMPLE_FILES, scope='session')
def example_file(request):
    return str(EXAMPLE_DIR / request.param)


@pytest.fixture(scope='session')
def example_obj(example_file):
    return file_to_obj(example_file)


@pytest.fixture(params=[0, 1], scope='session')
def entry_obj(example_obj, request):
    entry = example_obj['entries'][request.param]
    entry['_id'] = '111tests'  # Mock id as from db
    return entry


@pytest.fixture(scope='session')  # type: ignore  # Better support in PyCharm
async def client() -> AsyncClient:
    async with LifespanManager(app), \
            AsyncClient(app=app,
                        headers={'x-api-key': 'test'},
                        base_url="http://test") as client:
        yield client


@pytest.fixture(scope='session')
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope='session')
async def example_id(client) -> AsyncGenerator[str, None]:
    try:
        filename = EXAMPLE_DIR / 'example.ttl'
        with open(filename, 'rb') as fd:
            response = await client.post(
                "/import",
                files={'file': fd},
                params={
                    'release': 'PUBLIC',
                    'api_key': 'test',
                    'genre': ['gen', 'spe'],
                })
            assert response.status_code == HTTPStatus.CREATED
            doc_id = response.text

        # Wait for it ...
        await verify_upload(client, doc_id)

        # Use fixture
        yield str(doc_id)

        # Clean up
        with get_db_sync() as db:
            result = db.dicts.delete_one({'_id': ObjectId(doc_id)})
            assert result.deleted_count == 1

    finally:
        # Drop db
        _db_client_sync().drop_database(settings.MONGODB_DATABASE)


@pytest.fixture(scope='session')
async def example_entry_ids(example_id):
    with get_db_sync() as db:
        cursor = db.entry.find({'_dict_id': ObjectId(example_id)}, {'_id': True})
        ids = [str(i['_id']) for i in cursor]
    return ids


@pytest.fixture(scope='module')
async def entry_id(client, example_id):
    response = await client.get(f'/lemma/{example_id}/cat',
                                params={'offset': 0, 'limit': 1})
    entry_id = response.json()[0]['id']
    return entry_id
