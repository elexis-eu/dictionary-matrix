import time

import pytest

import app.linking.ops
from app.settings import _Settings

pytestmark = pytest.mark.asyncio


async def test_linking(client, example_id, monkeypatch, httpserver):
    TASK_ID = 'remote_task_id'
    httpserver \
        .expect_request('/submit') \
        .respond_with_data(TASK_ID)
    httpserver \
        .expect_request('/status', data=TASK_ID) \
        .respond_with_json({'state': 'COMPLETED',
                            'message': ''})
    LINKING_RESULT = [{
        "source_entry": "cat-n",
        "target_entry": "cat-EN",
        "linking": [{
            'source_sense': 'cat-n-1',
            'target_sense': '00016606n',
            'type': 'exact',
            'score': .8,
        }]
    }]
    httpserver \
        .expect_request('/result', data=TASK_ID) \
        .respond_with_json(LINKING_RESULT)

    # Mock Naisc server with `httpserver`
    monkeypatch.setattr(
        app.linking.ops, 'settings',
        _Settings(**dict(app.linking.ops.settings,
                         LINKING_NAISC_URL=httpserver.url_for('/'))))

    response = await client.post('/linking/submit',
                                 json={'source': {'id': example_id},
                                       'target': {'id': example_id}})
    assert not response.is_error, response.json()
    task_id = response.text
    assert task_id

    # Assume the other thread hadn't had time to process the request ...
    response = await client.post('/linking/status', content=task_id)
    assert not response.is_error, response.json()
    assert response.json()['state'] == 'PROCESSING'

    # ... but by now it did.
    time.sleep(.1)
    response = await client.post('/linking/status', content=task_id)
    assert not response.is_error, response.json()
    assert response.json()['state'] == 'COMPLETED'

    response = await client.post('/linking/result', content=task_id)
    assert not response.is_error, response.json()
    assert response.json() == LINKING_RESULT
