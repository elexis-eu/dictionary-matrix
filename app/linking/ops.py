import logging
import os
import time
import traceback
from functools import lru_cache
from tempfile import NamedTemporaryFile
from typing import List
from urllib.parse import urljoin

import httpx
from bson import ObjectId

from .models import (
    LinkingJob, LinkingJobPrivate, LinkingJobStatus, LinkingOneResult,
    LinkingSource, LinkingStatus,
)
from ..rdf import file_to_obj
from ..settings import settings
from ..db import get_db_sync, reset_db_client

log = logging.getLogger(__name__)

_BABELNET_ID = 'babelnet'


def _upstream_submit(service_url, job: LinkingJobPrivate) -> str:
    with httpx.Client() as client:
        submit_job = LinkingJob(**job.dict(exclude_unset=True, exclude_none=True))
        response = client.post(urljoin(service_url, 'submit'),
                               json=submit_job.json(exclude_unset=True, exclude_none=True))
    assert not response.is_error, response.status_code
    task_id = response.text
    return task_id


def _upstream_status(job: LinkingJobPrivate) -> LinkingStatus:
    with httpx.Client() as client:
        response = client.post(urljoin(job.service_url, 'status'),
                               content=job.remote_task_id)
    assert not response.is_error, response.status_code
    status = LinkingStatus(**response.json())
    return status


def _upstream_result(job: LinkingJobPrivate) -> List[dict]:
    with httpx.Client() as client:
        response = client.post(urljoin(job.service_url, 'result'),
                               content=job.remote_task_id)
    assert not response.is_error, response.status_code
    result = [LinkingOneResult(**i).dict() for i in response.json()]
    return result


@lru_cache(1)
def _local_endpoint():
    """URL to us, the source endpoint for this linking task"""
    from .router import router
    assert router.prefix
    return urljoin(settings.SITEURL, router.prefix)


def process_linking_job(id: str):  # noqa: C901
    reset_db_client()
    remote_task_id = None
    service_url = None
    our_result = None
    origin_result = None
    job = None
    new_status = LinkingStatus(state=LinkingJobStatus.FAILED, message='')
    try:
        # Get job handle
        while True:  # Maybe retry if db not yet synced
            with get_db_sync() as db:
                job = db.linking_jobs.find_one({'_id': ObjectId(id)})
                if job:
                    break
        job = LinkingJobPrivate(**job, id=job['_id'])

        # Set / check defaults
        is_babelnet = job.target.id == _BABELNET_ID
        assert job.source.id != _BABELNET_ID
        job.service_url = service_url = (settings.LINKING_BABELNET_URL
                                         if is_babelnet else
                                         settings.LINKING_NAISC_URL)
        with get_db_sync() as db:
            if not job.source.endpoint:
                assert db.dicts.find_one({'_id': ObjectId(job.source.id)}), \
                    f"Dictionary {job.source.id!r} not found here"
            if not job.target.endpoint and not is_babelnet:
                assert db.dicts.find_one({'_id': ObjectId(job.target.id)}), \
                    f"Dictionary {job.target.id!r} not found here"

        # Fetch linked entries to obtain a local copy
        origin_source_dict_id = None
        origin_target_dict_id = None
        if job.source.endpoint:
            origin_source_dict_id = job.source.id
            job.source = _get_entries(job.source)
        else:
            job.source.endpoint = _local_endpoint()
        if not is_babelnet:
            if job.target.endpoint:
                origin_target_dict_id = job.target.id
                job.target = _get_entries(job.target)
            else:
                job.target.endpoint = _local_endpoint()
        assert job.source.endpoint.startswith(_local_endpoint())
        assert is_babelnet or job.target.endpoint.startswith(_local_endpoint())

        # Submit task to the remote linking service
        job.remote_task_id = remote_task_id = _upstream_submit(service_url, job)
        assert remote_task_id

        # Wait for task completion
        while True:
            new_status = _upstream_status(job)
            if new_status.state in (LinkingJobStatus.COMPLETED,
                                    LinkingJobStatus.FAILED):
                log.debug('Linking task finished: '
                          'job %r (task %r) state %s, message: %r',
                          str(job.id), remote_task_id, new_status.state, new_status.message)
                result = _upstream_result(job)
                break

            time.sleep(30)

        # Convert results' to origin entry ids
        our_result = result
        origin_result = None
        if origin_source_dict_id or origin_target_dict_id:
            origin_result = our_result.copy()
            with get_db_sync() as db:
                for should_convert, our_dict_id, results_key in [
                    (origin_source_dict_id, job.source.id, 'source_entry'),
                    (origin_target_dict_id, job.target.id, 'target_entry'),
                ]:
                    if not should_convert:
                        continue
                    entries = list(db.entry.find(
                        {'_dict_id': ObjectId(our_dict_id), '_origin_id': {'$exists': True}},
                        {'_origin_id': True, 'senses': True}
                    ))
                    to_origin_id = {str(i['_id']): i['_origin_id']
                                    for i in entries}
                    for res in origin_result:
                        res[results_key] = to_origin_id[res[results_key]]

    except Exception:
        log.exception('Unexpected error for linking task %s: %s', id, job)
        new_status = LinkingStatus(state=LinkingJobStatus.FAILED,
                                   message=traceback.format_exc())
    finally:
        with get_db_sync() as db:
            db.linking_jobs.update_one(
                {'_id': ObjectId(id)},
                {'$set': dict(new_status,
                              our_result=our_result,
                              origin_result=origin_result,
                              service_url=service_url,
                              remote_task_id=remote_task_id)})


def _get_entries(source: LinkingSource) -> LinkingSource:  # noqa: C901
    def response_to_entry_obj(fmt: str, response: httpx.Response):
        if fmt == 'json':
            text = f'''\
                {{"{our_dict_id}": {{
                    "meta": {{
                        "release": "PRIVATE",
                        "sourceLanguage": "xx"
                    }},
                    "entries": [
                        {response.text}
                    ]
                }} }}'''
        elif fmt == 'ontolex':
            text = response.text  # Already valid input
        elif fmt == 'tei':
            text = f'''\
                <TEI xmlns="http://www.tei-c.org/ns/1.0">
                <teiHeader></teiHeader>
                <text><body>
                {response.text}
                </body></text></TEI>'''
        else:
            assert False, f'invalid fmt= {fmt!r}'

        with NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as fd:
            fd.write(text)
            filename = fd.name
        try:
            dict_obj = file_to_obj(filename)
        finally:
            os.remove(filename)
        entry_obj = dict_obj['entries'][0]
        return entry_obj

    def get_one_entry(origin_entry_id):
        result = db.entry.find_one({'_origin_id': origin_entry_id,
                                    '_dict_id': our_dict_id})
        if result:
            return str(result['_id'])

        for fmt in ('json', 'ontolex', 'tei'):
            response = client.get(urljoin(endpoint,
                                          f'{fmt}/{origin_dict_id}/{origin_entry_id}'))
            if not response.is_error:
                break
        else:
            raise ValueError('No suitable format for entry '
                             f'{origin_dict_id}/{origin_entry_id}')

        entry_obj = response_to_entry_obj(fmt, response)
        entry_obj['_dict_id'] = our_dict_id
        entry_obj['_origin_id'] = origin_entry_id

        result = db.entry.insert_one(entry_obj)
        our_entry_id = str(result.inserted_id)
        db.dicts.update_one({'_id': our_dict_id},
                            {'$push': {'_entries': our_entry_id}})
        return our_entry_id

    headers = {'X-API-Key': source.apiKey} if source.apiKey else {}
    assert source.endpoint
    endpoint = source.endpoint
    with get_db_sync() as db, \
            httpx.Client(headers=headers) as client:

        origin_dict_id = source.id
        result = db.dicts.find_one(
            {'_origin_id': origin_dict_id,
             '_origin_endpoint': endpoint}, {'_id': True})
        if result:
            our_dict_id = result['_id']
        else:
            response = client.get(urljoin(endpoint, f'about/{origin_dict_id}'))
            dict_obj = response.json()
            dict_obj['api_key'] = source.apiKey
            dict_obj['_origin_id'] = origin_dict_id
            dict_obj['_origin_endpoint'] = endpoint
            our_dict_id = db.dicts.insert_one(dict_obj).inserted_id

        origin_entry_ids = source.entries
        if not origin_entry_ids:
            response = client.get(urljoin(endpoint, f'list/{origin_dict_id}'))
            origin_entry_ids = [i['id'] for i in response.json()]

        # THIS, is absolutely not how it was supposed to be done
        our_entry_ids = [get_one_entry(i) for i in origin_entry_ids]

        new_source = LinkingSource(
            id=str(our_dict_id),
            endpoint=_local_endpoint(),
            apiKey=source.apiKey,
            # Don't request explicit entries if none were passed to us
            entries=our_entry_ids if source.entries else None)
        return new_source
