import asyncio
import logging
import threading
import traceback
from functools import wraps
from queue import Empty as QueueEmpty
from typing import List
from urllib.parse import urljoin

import httpx
from bson import ObjectId

from .models import (
    LinkingJob, LinkingJobPrivate, LinkingJobStatus, LinkingOneResult,
    LinkingStatus,
)
from ..settings import settings
from ..db import _DbType, get_db_sync, reset_db_client

log = logging.getLogger(__name__)

_BABELNET_ID = 'babelnet'


def coroutine_in_new_thread(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        reset_db_client()
        asyncio.set_event_loop(asyncio.new_event_loop())
        return asyncio.get_event_loop().run_until_complete(func(*args, **kwargs))
    return wrapper


@coroutine_in_new_thread  # noqa: C901
async def _linking_task_status_checker(queue, cv: threading.Condition):
    jobs: List[LinkingJobPrivate] = []

    async def check_one(job):
        new_status = LinkingStatus(state='FAILED', message='')
        result = None
        try:
            new_status = await upstream_status(job)
            if new_status.state == LinkingJobStatus.PROCESSING:
                return
            assert new_status.state in (LinkingJobStatus.COMPLETED,
                                        LinkingJobStatus.FAILED)
            log.debug('Linking task status checker thread finished: '
                      'job %r (task %r) state %s, message: %s',
                      job.id, job.remote_task_id, new_status.state, new_status.message)
            result = await upstream_result(job)
        except Exception:
            log.exception('Unexpected error for linking task %s', job)
            new_status.state = LinkingJobStatus.FAILED
            new_status.message = traceback.format_exc()

        jobs.remove(job)
        job_obj = new_status.dict()
        if result:
            job_obj['result'] = result
        with get_db_sync() as db:  # type: _DbType
            db.linking_jobs.update_one(
                {'_id': job.id}, {'$set': job_obj})

    while True:
        log.debug('Linking task status checker thread wakeup ...')

        # Pop new pending jobs from the queue
        while True:
            try:
                id = queue.get(block=False)
            except QueueEmpty:
                break
            log.debug('Linking task status checker thread got new job %r', id)
            while True:
                with get_db_sync() as db:  # type: _DbType
                    job = db.linking_jobs.find_one({'_id': ObjectId(id)})
                    if job:
                        break
            job = LinkingJobPrivate(**job, id=job['_id'])
            assert job.state == LinkingJobStatus.PROCESSING
            jobs.append(job)

        try:
            log.debug('Linking task status checker thread checking %d tasks', len(jobs))
            if jobs:
                await asyncio.gather(*[check_one(job) for job in list(jobs)])
        except Exception:
            log.exception('Unexpected exception')

        log.debug('Linking task status checker thread sleeping.')
        with cv:
            cv.wait(timeout=30)


async def upstream_submit(url: str, job: LinkingJob) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            urljoin(url, 'submit'),
            json=job.dict(exclude_unset=True, exclude_none=True))
    assert not response.is_error, response.status_code
    task_id = response.text
    return task_id


async def upstream_status(job: LinkingJobPrivate) -> LinkingStatus:
    async with httpx.AsyncClient() as client:
        response = await client.post(urljoin(job.service_url, 'status'),
                                     content=job.remote_task_id)
    assert not response.is_error, response.status_code
    status = LinkingStatus(**response.json())
    return status


async def upstream_result(job: LinkingJobPrivate) -> List[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.post(urljoin(job.service_url, 'result'),
                                     content=job.remote_task_id)
    assert not response.is_error, response.status_code
    result = [LinkingOneResult(**i).dict() for i in response.json()]
    return result


async def submit_linking_job(db: _DbType, job: LinkingJob, SITE_URL: str):
    assert job.source.id != _BABELNET_ID
    is_babelnet = job.target.id == _BABELNET_ID
    submit_job = job.copy()

    # Set task defaults
    async def _check_valid(dict_id):
        assert await db.dicts.find_one({'_id': ObjectId(dict_id)}), \
            f"Dictionary {dict_id!r} not found here"
    if not job.source.endpoint:
        submit_job.source.endpoint = SITE_URL
        await _check_valid(job.source.id)
    if not job.target.endpoint and not is_babelnet:
        submit_job.target.endpoint = SITE_URL
        await _check_valid(job.target.id)
    if not job.config:
        submit_job.config = {'foo': 'ontolex-default'}  # TODO

    # Submit task to remote service
    service_url = (settings.LINKING_BABELNET_URL if is_babelnet else
                   settings.LINKING_NAISC_URL)
    task_id = await upstream_submit(service_url, submit_job)
    assert task_id

    # Track in local db
    task = LinkingJobPrivate(**job.dict(exclude_unset=True, exclude_none=True),
                             remote_task_id=task_id,
                             service_url=service_url)
    result = await db.linking_jobs.insert_one(task.dict(exclude_unset=True, exclude_none=True))

    # Fetch linked entries to obtain a local copy
    if submit_job.source.endpoint != SITE_URL:
        assert False, 'Need source entries on _this_ endpoint'
    if submit_job.target.endpoint != SITE_URL and not is_babelnet:
        assert False, 'Need target entries on _this_ endpoint'

    return str(result.inserted_id)
