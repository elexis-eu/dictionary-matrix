from http import HTTPStatus

import pytest

from tests.conftest import verify_upload

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
