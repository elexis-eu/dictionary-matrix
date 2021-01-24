import logging
import time
import traceback
from typing import List
from urllib.parse import urljoin

import httpx
from bson import ObjectId

from .models import (
    LinkingJob, LinkingJobPrivate, LinkingJobStatus, LinkingOneResult,
    LinkingStatus,
)
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


def process_linking_job(id: str):  # noqa: C901
    reset_db_client()
    remote_task_id = None
    service_url = None
    result = None
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

        # TODO: Fetch linked entries to obtain a local copy
        if job.source.endpoint:
            assert False, 'Need source entries on _this_ endpoint'
        if job.target.endpoint and not is_babelnet:
            assert False, 'Need target entries on _this_ endpoint'

        # Submit task to the remote linking service
        job.remote_task_id = remote_task_id = _upstream_submit(service_url, job)
        assert remote_task_id

        # Wait for task completion
        while True:
            new_status = _upstream_status(job)
            if new_status.state in (LinkingJobStatus.COMPLETED,
                                    LinkingJobStatus.FAILED):
                log.debug('Linking task finished: '
                          'job %r (task %r) state %s, message: %s',
                          job.id, remote_task_id, new_status.state, new_status.message)
                result = _upstream_result(job)
                break

            time.sleep(30)

        # TODO: Use/convert the results
        if result:
            ...

    except Exception:
        log.exception('Unexpected error for linking task %s: %s', id, job)
        new_status = LinkingStatus(state=LinkingJobStatus.FAILED,
                                   message=traceback.format_exc())
    finally:
        with get_db_sync() as db:
            db.linking_jobs.update_one(
                {'_id': ObjectId(id)},
                {'$set': dict(new_status,
                              result=result,
                              service_url=service_url,
                              remote_task_id=remote_task_id)})
