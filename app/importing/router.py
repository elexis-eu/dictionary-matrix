import logging
import os
import shutil
from http import HTTPStatus
from queue import SimpleQueue
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .ops import _get_upload_filename, _process_one_dict
from .models import ImportJob, JobStatus, Url
from ..db import _DbType, get_db
from ..models import Genre, Language, ReleasePolicy
from ..settings import settings
from ..tasks import Task

log = logging.getLogger(__name__)

router = APIRouter()

_import_queue: SimpleQueue = SimpleQueue()


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
        job = ImportJob(
            url=url,
            file=upload_path,
            dict_id=dictionary and ObjectId(dictionary),
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
