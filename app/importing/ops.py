import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

import httpx
from bson import ObjectId

from .models import ImportJob, JobStatus
from ..settings import settings
from ..db import get_db_sync, reset_db_client, safe_path
from ..rdf import file_to_obj


def _get_upload_filename(username, filename) -> str:
    now = str(datetime.now()).replace(" ", "T")
    return str(Path(settings.UPLOAD_PATH) /
               f'{now}-{safe_path(username)}-{safe_path(filename)}')


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
