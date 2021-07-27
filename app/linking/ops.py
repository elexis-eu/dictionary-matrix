import logging
import os
import re
import subprocess
import time
import traceback
from collections import defaultdict
from functools import lru_cache
from tempfile import NamedTemporaryFile
from typing import List
from urllib.parse import urljoin

import httpx
from bson import ObjectId

from .models import (
    LinkingJob, LinkingJobPrivate, LinkingJobStatus, LinkingOneResult,
    LinkingStatus, SenseLink,
)
from ..importing.models import ApiImportJob, JobStatus
from ..importing.ops import _process_one_api
from ..rdf import add_entry_sense_ids, export_for_naisc, removeprefix
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
        assert job.service_url
        response = client.post(urljoin(job.service_url, 'status'),
                               content=job.remote_task_id)
    assert not response.is_error, response.status_code
    status = LinkingStatus(**response.json())
    return status


def _upstream_result(job: LinkingJobPrivate) -> List[dict]:
    with httpx.Client() as client:
        assert job.service_url
        response = client.post(urljoin(job.service_url, 'result'),
                               content=job.remote_task_id)
    assert not response.is_error, response.status_code
    result = [LinkingOneResult(**i).dict() for i in response.json()]
    return result


@lru_cache(1)
def _local_endpoint():
    """URL to us, the source endpoint for this linking task"""
    from .router import router
    assert router.prefix
    return urljoin(settings.SITEURL, router.prefix)


def _import_from_api(endpoint, remote_api_key, local_api_key, dict_id, entries):
    with get_db_sync() as db:
        job = ApiImportJob(
            url=endpoint,
            remote_dict_id=dict_id,
            remote_api_key=remote_api_key,
            api_key=local_api_key,
            state=JobStatus.SCHEDULED,
        )
        result = db.import_jobs.insert_one(job.dict())
        id = str(result.inserted_id)
        _process_one_api(id)

        while db.import_jobs.find_one({'_id': result.inserted_id,
                                       'state': JobStatus.SCHEDULED}) is not None:
            time.sleep(3)
        job_dict = db.import_jobs.find_one({'_id': result.inserted_id})
        job = ApiImportJob(**job_dict)
        assert job.state == JobStatus.DONE, job

        dict_id = str(job.id)
        entry_origin_ids = list(db.entry.find(
            {'_dict_id': ObjectId(dict_id), '_origin_id': {'$exists': True}},
            {'_origin_id': True}
        ))
        to_our_id = {i['_origin_id']: str(i['_id'])
                     for i in entry_origin_ids}
        our_entries = [to_our_id[i] for i in (entries or [])]
        return dict_id, our_entries


def process_linking_job(job_id: str):  # noqa: C901
    reset_db_client()
    remote_task_id = None
    service_url = None
    our_result = None
    origin_result = None
    job = None
    new_status = LinkingStatus(state=LinkingJobStatus.COMPLETED, message='')
    log.info('Start linking job %s', job_id)
    try:
        # Get job handle
        while True:  # Maybe retry if db not yet synced
            with get_db_sync() as db:
                job = db.linking_jobs.find_one({'_id': ObjectId(job_id)})
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

        # Fetch linked entries to obtain a local copy
        origin_source_dict_id = None
        origin_target_dict_id = None

        def ensure_local_source(source):
            DEFAULT_API_KEY = 'linking'
            local_api_key = source.apiKey or DEFAULT_API_KEY
            source.id, entries = _import_from_api(source.endpoint,
                                                  source.apiKey,
                                                  local_api_key,
                                                  source.id,
                                                  source.entries)
            if entries:
                source.entries = entries

        if job.source.endpoint:
            origin_source_dict_id = job.source.id
            ensure_local_source(job.source)
        job.source.endpoint = _local_endpoint()
        if not is_babelnet:
            if job.target.endpoint:
                origin_target_dict_id = job.target.id
                ensure_local_source(job.target)
            job.target.endpoint = _local_endpoint()
        assert job.source.endpoint.startswith(_local_endpoint())
        assert is_babelnet or job.target.endpoint.startswith(_local_endpoint())

        if not is_babelnet and settings.LINKING_NAISC_EXECUTABLE:
            # Naisc is run as local CLI command
            result = _linking_from_naisc_executable(job)
        else:
            # Submit task to the remote linking service
            assert service_url, service_url
            job.remote_task_id = remote_task_id = _upstream_submit(service_url, job)
            assert remote_task_id

            # Wait for task completion
            while True:
                new_status = _upstream_status(job)
                if new_status.state in (LinkingJobStatus.COMPLETED,
                                        LinkingJobStatus.FAILED):
                    log.debug('Linking task finished: '
                              'job %r (task %r) state %s, message: %r',
                              str(job.id), remote_task_id, new_status.state, new_status.message)
                    result = _upstream_result(job)
                    break

                time.sleep(30)

        # Convert results' to origin entry ids
        our_result = result
        origin_result = None
        if origin_source_dict_id or origin_target_dict_id:
            origin_result = our_result.copy()
            with get_db_sync() as db:
                for should_convert, our_dict_id, results_key in [
                    (origin_source_dict_id, job.source.id, 'source_entry'),
                    (origin_target_dict_id, job.target.id, 'target_entry'),
                ]:
                    if not should_convert:
                        continue
                    entries = list(db.entry.find(
                        {'_dict_id': ObjectId(our_dict_id), '_origin_id': {'$exists': True}},
                        {'_origin_id': True, 'senses': True}
                    ))
                    to_origin_id = {str(i['_id']): i['_origin_id']
                                    for i in entries}
                    for res in origin_result:
                        res[results_key] = to_origin_id[res[results_key]]

    except Exception:
        log.exception('Unexpected error for linking task %s: %s', job_id, job)
        new_status = LinkingStatus(state=LinkingJobStatus.FAILED,
                                   message=traceback.format_exc())
        our_result = []
    finally:
        assert our_result is not None
        n_links = sum(len(i['linking']) for i in our_result)
        log.debug('Linking job %s finished: total %d links '
                  'between %d pairs of entries found',
                  str(job_id), n_links, len(our_result))
        with get_db_sync() as db:
            db.linking_jobs.update_one(
                {'_id': ObjectId(job_id)},
                {'$set': dict(new_status,
                              our_result=our_result,
                              origin_result=origin_result,
                              service_url=service_url,
                              remote_task_id=remote_task_id)})


def _linking_from_naisc_executable(job) -> List[dict]:
    assert settings.LINKING_NAISC_EXECUTABLE
    assert job.source.endpoint.startswith(_local_endpoint())
    assert job.target.endpoint.startswith(_local_endpoint())
    temp_files = []
    sense_entry_mappings = []
    try:
        for id, entries in ((job.source.id, job.source.entries),
                            (job.target.id, job.target.entries)):
            with NamedTemporaryFile(suffix='.ttl', delete=False) as fd, \
                    get_db_sync() as db:
                temp_files.append(fd.name)
                if entries:
                    entries = db.entry.find({
                        '_id': {'$in': list(map(ObjectId, entries))}}, {'_dict_id': False})
                else:
                    entries = db.entry.find({'_dict_id': ObjectId(id)}, {'_dict_id': False})
                entries = list(entries)
                text = export_for_naisc(entries)
                fd.write(text.encode('utf-8'))
                entries = [add_entry_sense_ids(e) for e in entries]
                sense_entry_mappings.append({sense['@id']: str(entry['@id'])
                                             for entry in entries
                                             for sense in entry['senses']})

        log.info('Linking job %s: dict %s to %s', str(job.id), job.source.id, job.target.id)
        cmdline = [str(settings.LINKING_NAISC_EXECUTABLE),
                   '-c', 'configs/auto.json',
                   *temp_files]
        log.debug('Running Naisc: %s', ' '.join(cmdline))
        proc = subprocess.run(cmdline, capture_output=True, text=True)
        if (proc.returncode != 0 or
                re.search(r'at java\.base|NullPointerException|FAILED', proc.stderr)):
            raise RuntimeError('Naisc errored with:\n' + proc.stderr)
    finally:
        log.debug('Removing temporary files %s', temp_files)
        for file in temp_files:
            os.remove(file)

    # Interpret output
    sense_links = defaultdict(list)
    # Naisc output format:
    #     <left-filename#sense-id-1> <SKOS_NS#exactMatch> <right-filename#sense-id-2> . # 0.8000
    for line in proc.stdout.split('\n'):
        if not line.strip():
            continue
        left_id, match_type, right_id, score = re.sub(
            r'<.*?#(.*?)>\s<.*?#(.*?)>\s<.*?#(.*?)> *\. *# *([\d.]+)',
            r'\1\t\2\t\3\t\4', line).split('\t')
        score = float(score)  # type: ignore
        left_id = removeprefix(left_id)  # Strip base URI, if applicable
        right_id = removeprefix(right_id)
        match_type = {
            'exactMatch': 'exact',
            # TODO: Below TBD?
            'broadMatch': 'broader',
            'broader': 'broader',
            'narrowMatch': 'narrower',
            'narrower': 'narrower',
            'relatedMatch': 'related',
        }[match_type]

        sense_links[(
            sense_entry_mappings[0][left_id],
            sense_entry_mappings[1][right_id]
        )].append(
            SenseLink(source_sense=left_id,
                      target_sense=right_id,
                      type=match_type,
                      score=score))

    # Result as Linking API defines it
    result = [LinkingOneResult(source_entry=k1,
                               target_entry=k2,
                               linking=v).dict()
              for (k1, k2), v in sense_links.items()]
    return result
