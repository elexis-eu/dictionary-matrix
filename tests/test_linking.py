import re
import time
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import Response

import app.linking.ops
from app.settings import _Settings

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def forwarder(client, example_id, example_entry_ids):
    """A http server forwarding REST requests to `client`, bound to app under test."""
    httpserver = HTTPServer()
    httpserver \
        .expect_request(re.compile(r'/about/[0-9a-f]+')) \
        .respond_with_json((await client.get(f'/about/{example_id}')).json())
    httpserver \
        .expect_request(re.compile(r'/list/[0-9a-f]+')) \
        .respond_with_json((await client.get(f'/list/{example_id}')).json())
    json_responses = [Response((await client.get(f'/json/{example_id}/{id}')).content)
                      for id in example_entry_ids]
    json_responses_iter = iter(json_responses)
    httpserver \
        .expect_request(re.compile(rf'/json/{example_id}/[0-9a-f]+')) \
        .respond_with_handler(lambda _: next(json_responses_iter))
    try:
        httpserver.start()
        yield httpserver
    finally:
        httpserver.stop()


async def test_linking_local_endpoint(client, example_id, monkeypatch, httpserver):
    linking_result = [{
        "source_entry": "cat-n",
        "target_entry": "cat-EN",
        "linking": [{
            'source_sense': 'cat-n-1',
            'target_sense': '00016606n',
            'type': 'exact',
            'score': .8,
        }]
    }]
    await _test(client, example_id, monkeypatch, httpserver,
                endpoint=None, linking_result=linking_result)


async def test_linking_remote_endpoint(client, example_id, monkeypatch, httpserver, forwarder):
    # FIXME: Ideally, we would take entry ids from dicts in submitted
    # request, but shit aint so easy due to multiproc, so we just mostly cover.
    linking_result: list = []
    await _test(client, example_id, monkeypatch, httpserver,
                endpoint=forwarder.url_for('/'), linking_result=linking_result)
    forwarder.check_assertions()


async def test_linking_naisc_executable(client, example_id, monkeypatch, httpserver,
                                        example_entry_ids):
    linking_result = [{
        'source_entry': example_entry_ids[0],
        'target_entry': example_entry_ids[0],
        'linking': [{
            'source_sense': 'elexis:dict#cat-n-1',
            'target_sense': 'elexis:dict#cat-n-1',
            'type': 'exact',
            'score': 0.8,
        }],
    }]
    mock_naisc = str(Path(__file__).parent / '_mock_naisc.py')

    monkeypatch.setattr(
        app.linking.ops, 'settings',
        _Settings(**dict(app.linking.ops.settings,
                         LINKING_NAISC_EXECUTABLE=mock_naisc)))

    await _test(client, example_id, monkeypatch, httpserver,
                endpoint=None, linking_result=linking_result)


async def _test(client, example_id, monkeypatch, httpserver, endpoint, linking_result):
    TASK_ID = 'remote_task_id'
    httpserver \
        .expect_request('/submit') \
        .respond_with_data(TASK_ID)
    httpserver \
        .expect_request('/status', data=TASK_ID) \
        .respond_with_json({'state': 'COMPLETED',
                            'message': ''})
    httpserver \
        .expect_request('/result', data=TASK_ID) \
        .respond_with_json(linking_result)

    # Mock Naisc server with `httpserver`
    monkeypatch.setattr(
        app.linking.ops, 'settings',
        _Settings(**dict(app.linking.ops.settings,
                         LINKING_NAISC_URL=httpserver.url_for('/'))))

    payload = {
        'source': {'id': example_id},
        'target': {'id': example_id}
    }
    if endpoint:
        payload['source']['endpoint'] = endpoint
        payload['target']['endpoint'] = endpoint

    response = await client.post('/linking/submit', json=payload)
    assert not response.is_error, response.json()
    task_id = response.text
    assert task_id

    # Assume the other thread hadn't had time to process the request ...
    response = await client.post('/linking/status', content=task_id)
    assert not response.is_error, response.json()
    assert response.json()['state'] == 'PROCESSING', response.json()

    # ... but by now it did.
    time.sleep(.1)
    response = await client.post('/linking/status', content=task_id)
    assert not response.is_error, response.json()
    assert response.json()['state'] == 'COMPLETED', response.json()

    response = await client.post('/linking/result', content=task_id)
    assert not response.is_error, response.json()
    assert response.json() == linking_result, response.json()

    httpserver.check_assertions()
