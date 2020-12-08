import logging
import os
import shutil
import traceback
from datetime import datetime
from concurrent.futures.process import ProcessPoolExecutor
from http import HTTPStatus
from pathlib import Path
from queue import SimpleQueue
from threading import Thread
from typing import List, Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import AnyHttpUrl

from .db import _DbType, get_db, get_db_sync, reset_db_client
from .models import Genre, ISO639Language, ReleasePolicy
from .rdf import file_to_obj
from .settings import settings
from .utils import _AutoEnum, enum_values, safe_path


log = logging.getLogger(__name__)

router = APIRouter()

import_queue: SimpleQueue = SimpleQueue()


class JobStatus(str, _AutoEnum):
    SCHEDULED, ERROR, DONE = _AutoEnum._auto_range(3)


def _get_upload_filename(username, filename) -> str:
    now = str(datetime.now()).replace(" ", "T")
    return str(Path(settings.UPLOAD_PATH) /
               f'{now}-{safe_path(username)}-{safe_path(filename)}')


@router.post('/import',
             status_code=HTTPStatus.CREATED,
             response_model=str,
             summary='Import a new dictionary.',
             description='Import a new dictionary by direct file upload '
                         '<b>or</b> an URL from where the dictionary can be fetched.')
async def dict_import(
        db=Depends(get_db),  # TODO secure
        url: Optional[AnyHttpUrl] = Query(
            None,
            description='URL of the dictionary to fetch and import. See <em>file=</em>.',
        ),
        file: Optional[UploadFile] = File(
            None,
            description='Dictionary file to import. In either OntoLex/Turtle, '
                        'OntoLex/XML-RDF, or TEI/XML format.',
        ),
        api_key: str = Query(
            ..., description='API key of the user uploading the dictionary.'),
        release: ReleasePolicy = Query(
            ..., description='Dictionary release policy. '
                             f'One of: {", ".join(enum_values(ReleasePolicy))}.',
        ),
        language: ISO639Language = Query(
            None,
            description='Main dictionary language in ISO639. '
                        '<b>Required</b> if not specified in the file.',
        ),
        genre: List[Genre] = Query(
            None,
            description='Dictionary genre. '
                        f'One or more of: {", ".join(enum_values(Genre))}.')
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

    job = {
        'url': url,
        'file': upload_path,
        'state': JobStatus.SCHEDULED,
        'meta': {
            'release': release,
            'language': language,
            'genre': genre,
            'api_key': api_key,
        }
    }
    result = await db.import_jobs.insert_one(job)
    id = str(result.inserted_id)
    import_queue.put(id)
    return id


@router.on_event('startup')
def ensure_upload_dir():
    os.makedirs(settings.UPLOAD_PATH, exist_ok=True)


@router.on_event('startup')
def prepare_import_queue_and_start_workers():
    log.info('Init worker threads (file upload)')
    for i in range(settings.UPLOAD_N_WORKERS):
        Thread(
            target=_dict_import_worker,
            args=(import_queue,),
            name=f'process_upload_worker_{i}',
            daemon=True,  # join thread on process exit
        ).start()


def _dict_import_worker(queue):
    for id in iter(queue.get, None):  # type: str
        try:
            # We process the document in a subprocess.
            # The malicious document may crash its process (lxml is C),
            # and we don't want that to affect the app, do we?
            with ProcessPoolExecutor(max_workers=1,
                                     initializer=reset_db_client) as executor:
                executor.submit(_process_one_dict, id).result(
                    timeout=settings.UPLOAD_TIMEOUT_SECONDS)
        except Exception:
            log.exception('Unexpected exception on %s:', id)


def _process_one_dict(id: str):
    id = ObjectId(id)
    log = logging.getLogger(__name__)
    log.debug('Start import job %s', id)
    with get_db_sync() as db:  # type: _DbType
        job = db.import_jobs.find_one({'_id': id})
        assert job['state'] == JobStatus.SCHEDULED

        url = job['url']
        filename = job['file']
        api_key = job['meta']['api_key']
        language = job['meta']['language']

        try:
            if url and not filename:
                log.debug('Download %s from %r', id, url)
                filename = _get_upload_filename(api_key, url)
                with httpx.stream("GET", url) as response:
                    num_bytes_expected = int(response.headers["Content-Length"])
                    with open(filename, 'wb') as fd:
                        for chunk in response.iter_bytes():
                            fd.write(chunk)
                assert response.num_bytes_downloaded == num_bytes_expected
                job['file'] = filename

            # Parse file
            assert filename
            log.debug('Parse %s from %r', id, filename)
            obj = file_to_obj(filename, language)

            # Transfer properties
            obj['_id'] = id
            obj.update(job['meta'])

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
