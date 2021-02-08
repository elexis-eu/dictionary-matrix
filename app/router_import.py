import logging
import os
import shutil
import traceback
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from queue import SimpleQueue
from typing import List, Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .db import _DbType, get_db, get_db_sync, reset_db_client, safe_path
from .models import Genre, ImportJob, JobStatus, Language, ReleasePolicy, Url
from .rdf import file_to_obj
from .settings import settings
from .tasks import Task

log = logging.getLogger(__name__)

router = APIRouter()

_import_queue: SimpleQueue = SimpleQueue()


def _get_upload_filename(username, filename) -> str:
    now = str(datetime.now()).replace(" ", "T")
    return str(Path(settings.UPLOAD_PATH) /
               f'{now}-{safe_path(username)}-{safe_path(filename)}')


@router.post('/import',
             status_code=HTTPStatus.CREATED,
             response_model=str,
             response_class=PlainTextResponse,
             summary='Import a new dictionary.',
             description='Import a new dictionary by direct file upload '
                         '<b>or</b> an URL from where the dictionary can be fetched.')
async def dict_import(
        db: _DbType = Depends(get_db),  # TODO secure it
        url: Optional[Url] = Query(
            None,
            description='URL of the dictionary to fetch and import. See <em>file=</em>.',
        ),
        file: Optional[UploadFile] = File(
            None,
            description='Dictionary file to import. In either OntoLex/Turtle, '
                        'OntoLex/XML/RDF, TEI/XML, or JSON format.',
        ),
        api_key: str = Query(
            ..., description='API key of the user uploading the dictionary.'),
        release: ReleasePolicy = Query(
            ..., description='Dictionary release policy. '
                             f'One of: {", ".join(ReleasePolicy.values())}.',
        ),
        sourceLanguage: Language = Query(
            None,
            description='Main dictionary language in ISO 639 2-alpha or 3-alpha. '
                        '<b>Required</b> if not specified in the file.',
        ),
        genre: List[Genre] = Query(
            None,
            description='Dictionary genre. '
                        f'One or more of: {", ".join(Genre.values())}.')
):
    if bool(url) == bool(file):
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST,
                            detail="Need either url= or file=")

    # If file, copy spooled file to UPLOAD_PATH
    upload_path = None
    if file:
        upload_path = _get_upload_filename(api_key, file.filename)
        with open(upload_path, 'wb') as fd:
            shutil.copyfileobj(file.file, fd, 10_000_000)
    try:
        job = ImportJob(
            url=url,
            file=upload_path,
            state=JobStatus.SCHEDULED,
            meta=dict(
                release=release,
                sourceLanguage=sourceLanguage,
                genre=genre,
                api_key=api_key,
            ))
    except Exception as e:
        log.exception('Invalid request params: %s', e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(e))
    result = await db.import_jobs.insert_one(job.dict())
    id = str(result.inserted_id)
    _import_queue.put(id)
    return id


@router.on_event('startup')
def ensure_upload_dir():
    os.makedirs(settings.UPLOAD_PATH, exist_ok=True)


@router.on_event('startup')
def prepare_import_queue_and_start_workers():
    Task(target=_process_one_dict,
         queue=_import_queue,
         n_workers=settings.UPLOAD_N_WORKERS,
         name='dict_import',
         timeout=settings.UPLOAD_TIMEOUT_SECONDS).start()


def _process_one_dict(id: str):
    id = ObjectId(id)
    log = logging.getLogger(__name__)
    log.debug('Start import job %s', id)
    reset_db_client()
    with get_db_sync() as db:
        job = db.import_jobs.find_one({'_id': id})
        job = ImportJob(**job)
        assert job.state == JobStatus.SCHEDULED
        filename = job.file
        try:
            # Download
            if job.url and not filename:
                log.debug('Download %s from %r', id, job.url)
                filename = _get_upload_filename(job.meta.api_key, job.url)
                with httpx.stream("GET", job.url) as response:
                    num_bytes_expected = int(response.headers["Content-Length"])
                    with open(filename, 'wb') as fd:
                        for chunk in response.iter_bytes():
                            fd.write(chunk)
                assert response.num_bytes_downloaded == num_bytes_expected
                job.file = filename

            # Parse file
            assert filename
            log.debug('Parse %s from %r', id, filename)
            obj = file_to_obj(filename, job.meta.sourceLanguage)

            # Transfer properties
            obj['_id'] = id
            # We add job.meta properrties on base object, which in
            # router /about get overriden by meta from file
            obj.update(job.meta.dict(exclude_none=True, exclude_unset=True))

            # Extract entries separately, assign them dict id
            entries = obj.pop('entries')
            assert entries, 'No entries in dictionary'
            for entry in entries:
                entry['_dict_id'] = id

            obj['n_entries'] = len(entries)

            # Insert dict, entries
            log.debug('Insert %s with %d entries', id, len(entries))
            result = db.entry.insert_many(entries)
            obj['_entries'] = result.inserted_ids  # Inverse of _dict_id
            result = db.dicts.insert_one(obj)
            assert result.inserted_id == id

            # Mark job done
            db.import_jobs.update_one(
                {'_id': id}, {'$set': {'state': JobStatus.DONE}})
            if settings.UPLOAD_REMOVE_ON_SUCCESS:
                os.remove(filename)
            log.debug('Done %s', id)

        except Exception:
            log.exception('Error processing %s', id)
            db.import_jobs.update_one(
                {'_id': id}, {'$set': {'state': JobStatus.ERROR,
                                       'error': traceback.format_exc()}})
            if settings.UPLOAD_REMOVE_ON_FAILURE and os.path.isfile(filename):
                os.remove(filename)
