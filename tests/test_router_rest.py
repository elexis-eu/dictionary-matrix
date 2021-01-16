from http import HTTPStatus

import pytest

from tests.test_rdf import assert_jsonld, assert_tei, assert_turtle

pytestmark = pytest.mark.asyncio


async def test_root(client):
    response = await client.get("/")
    assert HTTPStatus.OK == response.status_code
    assert 'ELEXIS' in response.text
    assert 'openapi.json' in response.text


# TODO: Add more specific tests once APIs stabilize.

async def test_dictionaries(client, example_id):
    response = await client.get('/dictionaries')
    assert response.status_code == HTTPStatus.OK
    assert 'dictionaries' in response.json()
    assert example_id in response.json()['dictionaries']


async def test_about(client, example_id):
    response = await client.get(f'/about/{example_id}')
    assert 'license' in response.json()


async def test_list(client, example_id):
    response = await client.get(f'/list/{example_id}')
    obj = response.json()
    assert len(obj)
    assert 'partOfSpeech' in obj[0]


async def test_lemma(client, example_id):
    response = await client.get(f'/lemma/{example_id}/cat')
    obj = response.json()
    assert len(obj)
    assert 'partOfSpeech' in obj[0]


@pytest.fixture(scope='module')
async def entry_id(client, example_id):
    response = await client.get(f'/lemma/{example_id}/cat',
                                params={'offset': 0, 'limit': 1})
    entry_id = response.json()[0]['id']
    return entry_id


async def test_entry_tei(client, example_id, entry_id):
    response = await client.get(f'/tei/{example_id}/{entry_id}')
    assert 'text/xml' in response.headers['content-type']
    assert_tei(response.text)


async def test_entry_jsonld(client, example_id, entry_id):
    response = await client.get(f'/json/{example_id}/{entry_id}')
    assert 'jsonld' in response.headers['Link']
    assert 'application/ld+json' == response.headers['content-type']
    assert_jsonld(response.json())


async def test_jsonld_context(client):
    response = await client.get('/context.jsonld')
    assert 'ontolex' in response.json()
    assert 'application/ld+json' in response.headers['content-type']


async def test_entry_turtle(client, example_id, entry_id):
    response = await client.get(f'/ontolex/{example_id}/{entry_id}')
    assert 'text/turtle' in response.headers['content-type']
    assert_turtle(response.text)
