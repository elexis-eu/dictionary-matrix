import logging
import threading
from http import HTTPStatus
from queue import SimpleQueue
from threading import Thread
from typing import List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .models import LinkingJob, LinkingOneResult, LinkingStatus
from .ops import _linking_task_status_checker, submit_linking_job
from ..db import _DbType, get_db


log = logging.getLogger(__name__)

router = APIRouter(prefix='/linking')

_linking_queue: SimpleQueue = SimpleQueue()
_cv: threading.Condition = None  # type: ignore


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
    task_id = await submit_linking_job(db, job, SITE_URL)
    _linking_queue.put(task_id)
    with _cv:
        _cv.notify()
    return task_id


@router.on_event('startup')
def init_linking_task_status_checker():
    log.info('Init linking task status checker thread')
    global _cv
    _cv = threading.Condition()
    Thread(
        target=_linking_task_status_checker,
        args=(_linking_queue, _cv),
        name='linking_task_status_checker',
        daemon=True,  # join thread on process exit
    ).start()


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
        {'_id': ObjectId(task_id)}, {'result': False})
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
    if not job or not job['result']:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND)
    return job['result']
