import logging
import os
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Tuple
from urllib.parse import urljoin

import httpx
import orjson
from bson import ObjectId

from .models import ApiImportJob, FileImportJob, JobStatus
from ..models import Dictionary, RdfFormats, ReleasePolicy
from ..settings import settings
from ..db import get_db_sync, reset_db_client, safe_path
from ..rdf import file_to_obj


def _get_upload_filename(username, filename) -> str:
    now = str(datetime.now()).replace(" ", "T")
    return str(Path(settings.UPLOAD_PATH) /
               f'{now}-{safe_path(username)}-{safe_path(filename)}')


def _process_one_file(job_id: str):
    job_id = ObjectId(job_id)
    log = logging.getLogger(__name__)
    log.info('Start file import job %s', job_id)
    reset_db_client()
    with get_db_sync() as db:
        job = db.import_jobs.find_one({'_id': job_id})
        job = FileImportJob(**job)
        assert job.state == JobStatus.SCHEDULED
        filename = job.file
        try:
            # Download
            if job.url and not filename:
                log.debug('Download %s from %r', job_id, job.url)
                filename = _get_upload_filename(job.api_key, job.url)
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

            # We add job.meta properties on base object, which in
            # router /about get overriden by meta from file
            obj.update(job.meta.dict(exclude_none=True, exclude_unset=True))

            _create_or_update_dict(db, obj, job, log, job.dict_id)

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


def _process_one_api(job_id: str):  # noqa: C901

    def _response_to_entry_obj(fmt: RdfFormats, response: httpx.Response):
        if fmt == RdfFormats.JSON:
            obj = response.json()
            obj.pop('@context', None)
            text = '''\
            {
              "dummy": {
                "meta": {
                  "release": "PRIVATE",
                  "sourceLanguage": "xx"
                },
                "entries": [ %s ]
              }
            }''' % orjson.dumps(obj).decode()
        elif fmt == RdfFormats.ONTOLEX:
            text = response.text  # Already valid input
        elif fmt == RdfFormats.TEI:
            text = f'''\
                <TEI xmlns="http://www.tei-c.org/ns/1.0">
                <teiHeader></teiHeader>
                <text><body>
                {response.text}
                </body></text></TEI>'''
        else:
            assert False, fmt

        with NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as fd:
            fd.write(text)
            filename = fd.name
        try:
            dict_obj = file_to_obj(filename)
        finally:
            os.remove(filename)
        entry_obj = dict_obj['entries'][0]
        return entry_obj

    def get_one_entry(origin_entry_obj):
        time.sleep(.05)  # Poor-man's rate limit. 20 rq/s will d/l 64k dict in ~an hour.
        origin_entry_id = origin_entry_obj['id']
        fmt = sorted(origin_entry_obj['formats'], key=RdfFormats.sort_key())[0]
        response = client.get(urljoin(endpoint,
                                      f'{fmt}/{origin_dict_id}/{origin_entry_id}'))
        entry_obj = _response_to_entry_obj(fmt, response)
        entry_obj['_origin_id'] = origin_entry_id
        return entry_obj

    job_id = ObjectId(job_id)
    log = logging.getLogger(__name__)
    log.info('Start API import job %s', job_id)
    reset_db_client()
    with get_db_sync() as db:
        job = db.import_jobs.find_one({'_id': job_id})
        job = ApiImportJob(**job)
        assert job.state == JobStatus.SCHEDULED
        endpoint = job.url
        origin_dict_id = job.remote_dict_id

        headers = {'X-API-Key': job.remote_api_key} if job.remote_api_key else None
        with httpx.Client(headers=headers, timeout=10) as client:
            log.debug('Import job %s, dict %r from %r', job_id, origin_dict_id, endpoint)
            entry = None
            try:
                response = client.get(urljoin(endpoint, f'about/{origin_dict_id}'))
                dict_obj = {
                    'meta': Dictionary(**response.json()).dict(exclude_none=True,
                                                               exclude_unset=True),
                    '_origin_id': origin_dict_id,
                    '_origin_endpoint': endpoint,
                    '_origin_api_key': job.remote_api_key,
                }

                response = client.get(urljoin(endpoint, f'list/{origin_dict_id}'))
                entries_list = response.json()
                # THIS, is absolutely not how it was supposed to be done
                entries = []
                for entry in entries_list:
                    if entry.get('release', ReleasePolicy.PUBLIC) == ReleasePolicy.PUBLIC:
                        entries.append(get_one_entry(entry))

                dict_obj['entries'] = entries

                _create_or_update_dict(db, dict_obj, job, log, job.dict_id or None)

                # Mark job done
                db.import_jobs.update_one(
                    {'_id': job_id}, {'$set': {'state': JobStatus.DONE}})
                log.debug('Done job %s', job_id)
            except Exception:
                log.error('Job %s failed on entry %s', job_id, entry)
                log.exception('Error processing job %s', job_id)
                db.import_jobs.update_one(
                    {'_id': job_id}, {'$set': {'state': JobStatus.ERROR,
                                               'error': traceback.format_exc()}})


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


def _create_or_update_dict(db, dict_obj, job, log, override_dict_id):
    # Transfer properties
    dict_obj['_id'] = job.id
    dict_obj['api_key'] = job.api_key
    dict_obj['_import_time'] = str(datetime.now())

    # Check if our dict should replace entries from other dict_id
    dict_id = override_dict_id or job.id
    if override_dict_id is not None:
        log.debug('Job %s replaces dict %s', job.id, dict_id)
        dict_obj['_id'] = dict_id

        old_obj = db.dicts.find_one({'api_key': job.api_key,
                                     '_id': override_dict_id},
                                    {'_id': True})
        if old_obj is None:
            raise Exception('E403, forbidden')

        # Transfer entry ids from old dict
        dict_obj = _transfer_ids(dict_obj, override_dict_id, db)

    # Extract entries separately, assign them dict id
    entries = dict_obj.pop('entries')
    assert entries, 'No entries in dictionary'
    dict_obj['n_entries'] = len(entries)
    for entry in entries:
        entry['_dict_id'] = dict_id

    log.info('Insert %s (api_key: %s) with %d entries', dict_id, job.api_key, len(entries))
    # Remove previous dict/entries
    db.entry.delete_many({'_dict_id': dict_id})
    db.dicts.delete_one({'_id': dict_id})

    # Insert dict, entries
    result = db.entry.insert_many(entries)
    dict_obj['_entries'] = result.inserted_ids  # Inverse of _dict_id
    result = db.dicts.insert_one(dict_obj)
    assert result.inserted_id == dict_id
