import asyncio
from http import HTTPStatus
from io import BytesIO

import pytest

from tests.conftest import EXAMPLE_DIR, verify_upload

pytestmark = pytest.mark.asyncio


async def test_from_file(client, example_file):
    with open(example_file, 'rb') as fd:
        response = await client.post(
            "/import",
            files={'file': fd},
            params={
                'release': 'PUBLIC',
                'api_key': 'test',
            })
    assert response.status_code == HTTPStatus.CREATED
    await verify_upload(client, response.text)


async def test_from_url(client, example_file, httpserver):
    with open(example_file) as fd:
        httpserver.expect_request('/some/file').respond_with_data(fd.read())
    response = await client.post(
        "/import",
        params={
            'url': httpserver.url_for('/some/file'),
            'release': 'PUBLIC',
            'api_key': 'test',
        })
    assert response.status_code == HTTPStatus.CREATED
    await verify_upload(client, response.text)


async def test_replace_dict(client, example_id, entry_id):
    with open(EXAMPLE_DIR / 'example.ttl', 'rb') as fd:
        text = fd.read()
    fd = BytesIO(text.replace(b'type of animal', b'lalala'))

    response = await client.post(
        "/import",
        files={'file': fd},
        params={
            'release': 'PUBLIC',
            'api_key': 'test',
            'dictionary': example_id,
        })
    assert response.status_code == HTTPStatus.CREATED
    await asyncio.sleep(1)  # Previous dict is still loaded. Wait longer.
    import time
    time.sleep(1)
    await verify_upload(client, example_id)

    response = await client.get(f'/json/{example_id}/{entry_id}')
    assert 'lalala' in str(response.read())
