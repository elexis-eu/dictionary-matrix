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

from app import app, settings  # noqa: E402
from app.db import _db_client, get_db  # noqa: E402


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


@pytest.fixture(params=EXAMPLE_FILES)
def example_file(request):
    return str(EXAMPLE_DIR / request.param)


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
            doc_id = response.json()

        # Wait for it ...
        await verify_upload(client, doc_id)

        # Use fixture
        yield str(doc_id)

        # Clean up
        _coro = get_db()
        db = await _coro.__anext__()
        result = await db.dicts.delete_one({'_id': ObjectId(doc_id)})
        assert result.deleted_count == 1

    finally:
        # Drop db
        await _db_client().drop_database(settings.MONGODB_DATABASE)
