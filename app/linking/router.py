import logging
import multiprocessing as mp
from http import HTTPStatus
from queue import SimpleQueue
from threading import Thread
from typing import List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .models import LinkingJob, LinkingOneResult, LinkingStatus
from .ops import process_linking_job
from ..db import _DbType, get_db


log = logging.getLogger(__name__)

router = APIRouter(prefix='/linking')

_linking_queue: SimpleQueue = SimpleQueue()


@router.post('/submit',
             status_code=HTTPStatus.CREATED,
             response_class=PlainTextResponse,
             response_model=str,
             summary='Submit a linking task.')
async def submit(
        job: LinkingJob,
        request: Request,
        db: _DbType = Depends(get_db),
):
    SITE_URL = str(request.url.replace(path='/', query='', fragment=''))
    # Set endpoints to None for local dicts
    if job.source.endpoint == SITE_URL:
        job.source.endpoint = None
    if job.target.endpoint == SITE_URL:
        job.target.endpoint = None

    result = await db.linking_jobs.insert_one(job.dict(exclude_unset=True, exclude_none=True))
    job_id = str(result.inserted_id)
    _linking_queue.put(job_id)
    return job_id


@router.on_event('startup')
def init_linking_task_worker():
    log.info('Init linking task worker thread')
    Thread(
        target=_linking_worker,
        args=(_linking_queue,),
        name='linking_task_worker',
        daemon=True,  # join thread on process exit
    ).start()


def _linking_worker(queue):
    processes = []
    for id in iter(queue.get, None):  # type: str
        proc = mp.Process(
            target=process_linking_job,
            args=(id,),
            name=f'linking_task_worker_{id}',
            daemon=True,  # join on process exit
        )
        proc.start()
        processes.append(proc)
        # Reap dead children
        for proc in list(processes):
            if not proc.is_alive():
                proc.join()
                processes.remove(proc)


@router.post('/status',
             status_code=HTTPStatus.OK,
             response_model=LinkingStatus,
             summary='Get the status of a linking task.')
async def status(
        request: Request,
        db: _DbType = Depends(get_db),
):
    task_id = (await request.body()).decode()
    job = await db.linking_jobs.find_one(
        {'_id': ObjectId(task_id)}, {'our_result': False,
                                     'origin_result': False})
    if not job:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    return job


@router.post('/result',
             status_code=HTTPStatus.OK,
             response_model=List[LinkingOneResult],
             summary='Get the result of a linking task.')
async def result(
        request: Request,
        db: _DbType = Depends(get_db),
):
    task_id = (await request.body()).decode()
    job = await db.linking_jobs.find_one({'_id': ObjectId(task_id)})
    try:
        return job['origin_result'] or job['our_result']
    except KeyError:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
