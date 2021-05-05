import logging
import os
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

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


def _process_one_dict(job_id: str):
    job_id = ObjectId(job_id)
    log = logging.getLogger(__name__)
    log.debug('Start import job %s', job_id)
    reset_db_client()
    with get_db_sync() as db:
        job = db.import_jobs.find_one({'_id': job_id})
        job = ImportJob(**job)
        assert job.state == JobStatus.SCHEDULED
        filename = job.file
        try:
            # Download
            if job.url and not filename:
                log.debug('Download %s from %r', job_id, job.url)
                filename = _get_upload_filename(job.meta.api_key, job.url)
                with httpx.stream("GET", job.url) as response:
                    num_bytes_expected = int(response.headers["Content-Length"])
                    with open(filename, 'wb') as fd:
                        for chunk in response.iter_bytes():
                            fd.write(chunk)
                assert response.num_bytes_downloaded == num_bytes_expected
                job.file = filename

            # Parse file into dict object
            assert filename
            log.debug('Parse %s from %r', job_id, filename)
            obj = file_to_obj(filename, job.meta.sourceLanguage)

            # Transfer properties
            obj['_id'] = job_id
            obj['import_time'] = str(datetime.now())
            # We add job.meta properrties on base object, which in
            # router /about get overriden by meta from file
            obj.update(job.meta.dict(exclude_none=True, exclude_unset=True))

            # Check if our dict should replace entries from other dict_id
            dict_id = job.dict_id or job_id
            if job.dict_id is not None:
                log.debug('Job %s replaces dict %s', job_id, dict_id)
                obj['_id'] = dict_id

                old_obj = db.dicts.find_one({'api_key': job.meta.api_key,
                                             '_id': dict_id},
                                            {'_id': True})
                if old_obj is None:
                    raise Exception('E403, forbidden')

                # Transfer entry ids from old dict
                obj = _transfer_ids(obj, dict_id, db)

            # Extract entries separately, assign them dict id
            entries = obj.pop('entries')
            assert entries, 'No entries in dictionary'
            obj['n_entries'] = len(entries)
            for entry in entries:
                entry['_dict_id'] = dict_id

            log.debug('Insert %s with %d entries', dict_id, len(entries))
            # Remove previous dict/entries
            db.entry.delete_many({'_dict_id': dict_id})
            db.dicts.delete_one({'_id': dict_id})

            # Insert dict, entries
            result = db.entry.insert_many(entries)
            obj['_entries'] = result.inserted_ids  # Inverse of _dict_id
            result = db.dicts.insert_one(obj)
            assert result.inserted_id == dict_id

            # Mark job done
            db.import_jobs.update_one(
                {'_id': job_id}, {'$set': {'state': JobStatus.DONE}})
            if settings.UPLOAD_REMOVE_ON_SUCCESS:
                os.remove(filename)
            log.debug('Done %s', job_id)

        except Exception:
            log.exception('Error processing %s', job_id)
            db.import_jobs.update_one(
                {'_id': job_id}, {'$set': {'state': JobStatus.ERROR,
                                           'error': traceback.format_exc()}})
            if settings.UPLOAD_REMOVE_ON_FAILURE and os.path.isfile(filename):
                os.remove(filename)


def _transfer_ids(new_obj, old_dict_id, db):
    def entry_to_key(entry):
        key = (
            entry['lemma'],
            entry['partOfSpeech'],
        )
        entry_counter[key] += 1  # Handles multiple equal <lemma,pos> entries
        return (
            *key,
            entry_counter[key],
        )

    entry_counter: Dict[Tuple[str, str], int] = defaultdict(int)
    old_entries = db.entry.find({'_dict_id': old_dict_id},
                                {'lemma': True,
                                 'partOfSpeech': True})
    old_id_by_key = {entry_to_key(entry): entry['_id']
                     for entry in old_entries}
    entry_counter.clear()
    for entry in new_obj['entries']:
        id = old_id_by_key.get(entry_to_key(entry))
        if id is not None:
            entry['_id'] = id
    return new_obj
