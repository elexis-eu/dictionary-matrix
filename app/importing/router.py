import logging
import os
import shutil
from http import HTTPStatus
from queue import SimpleQueue
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .ops import _get_upload_filename, _process_one_api, _process_one_file
from .models import ApiImportJob, FileImportJob, JobStatus, Url
from ..db import _DbType, get_db
from ..models import Genre, Language, ReleasePolicy
from ..settings import settings
from ..tasks import Task

log = logging.getLogger(__name__)

router = APIRouter()

_file_import_queue: SimpleQueue = SimpleQueue()
_api_import_queue: SimpleQueue = SimpleQueue()


@router.post('/import',
             status_code=HTTPStatus.CREATED,
             response_model=str,
             response_class=PlainTextResponse,
             summary='Import a new dictionary.',
             description='Import a new dictionary by direct file upload <b>or</b> '
                         'an URL where the dictionary file can be fetched from.')
async def file_url_import(
        db: _DbType = Depends(get_db),  # TODO secure it
        url: Optional[Url] = Query(
            None,
            description='URL of the dictionary to fetch and import. See <em>file=</em>.',
        ),
        dictionary: Optional[str] = Query(
            None,
            description='Id of dictionary to replace.',
            regex='^[a-z0-f]{24}$',
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
        job = FileImportJob(
            url=url,
            file=upload_path,
            dict_id=dictionary and ObjectId(dictionary),
            state=JobStatus.SCHEDULED,
            api_key=api_key,
            meta=dict(
                release=release,
                sourceLanguage=sourceLanguage,
                genre=genre,
            ))
    except Exception as e:
        log.exception('Invalid request params: %s', e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(e))
    result = await db.import_jobs.insert_one(job.dict())
    id = str(result.inserted_id)
    _file_import_queue.put(id)
    return id


@router.post('/api_import',
             status_code=HTTPStatus.CREATED,
             response_model=str,
             response_class=PlainTextResponse,
             summary='Import a new dictionary via Elexis REST API.',
             description='Import a dictionary from another Elexis REST API endpoint.')
async def api_import(
        db: _DbType = Depends(get_db),
        url: Url = Query(
            None,
            description='URL of the Elexis API endpoint.',
        ),
        remote_dictionary: str = Query(
            ...,
            description='Id of dictionary to replace.',
        ),
        local_dictionary: Optional[str] = Query(
            None,
            description='Id of dictionary to replace.',
            regex='^[a-z0-f]{24}$',
        ),
        remote_api_key: Optional[str] = Query(
            None, description='API key to access the remote dictionary.'),
        local_api_key: str = Query(
            ..., description='API key of the local user.'),
):
    try:
        job = ApiImportJob(
            url=url,
            remote_dict_id=remote_dictionary,
            dict_id=local_dictionary and ObjectId(local_dictionary),
            remote_api_key=remote_api_key,
            api_key=local_api_key,
            state=JobStatus.SCHEDULED,
        )
    except Exception as e:
        log.exception('Invalid request params: %s', e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(e))
    result = await db.import_jobs.insert_one(job.dict())
    id = str(result.inserted_id)
    _api_import_queue.put(id)
    return id


@router.on_event('startup')
def ensure_upload_dir():
    os.makedirs(settings.UPLOAD_PATH, exist_ok=True)


@router.on_event('startup')
def prepare_import_queue_and_start_workers():
    Task(target=_process_one_file,
         queue=_file_import_queue,
         n_workers=settings.UPLOAD_N_WORKERS,
         name='file_import',
         timeout=settings.UPLOAD_TIMEOUT_SECONDS).start()
    Task(target=_process_one_api,
         queue=_api_import_queue,
         n_workers=settings.API_IMPORT_N_WORKERS,
         name='api_import',
         timeout=12*settings.API_IMPORT_TIMEOUT_SECONDS).start()
