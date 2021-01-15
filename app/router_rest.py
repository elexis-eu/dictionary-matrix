import itertools
import logging
from http import HTTPStatus
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Header, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import ORJSONResponse, Response

from .db import get_db
from .models import Dictionaries, Dictionary, Lemma, PartOfSpeech
from .rdf import JSONLD_CONTEXT, entry_to_jsonld, entry_to_ontolex, entry_to_tei

log = logging.getLogger(__name__)

router = APIRouter()

_APIKEY_HEADER = Header(
    ..., alias='X-API-Key',
    description='An API key to authorize access to this endpoint if necessary')
_DICT_PATH: str = Path(
    ...,
    description='Dictionary id.',
    regex=r'[a-f\d]{24}',
)
_OFFSET_QUERY = Query(0, ge=0)
_LIMIT_QUERY = Query(1_000_000, ge=1)


async def _get_db_verify_api_key(
        request: Request,
        db=Depends(get_db),
        api_key=_APIKEY_HEADER,
        dictionary: str = _DICT_PATH,
):
    """
    Verify `api_key` is allowed to access `dictionary` and
    cache positive result in user's session cookie.
    """
    user_dicts = request.session.get('dicts', [])
    if dictionary not in user_dicts:
        if not await db.dicts.find_one({'_id': ObjectId(dictionary),
                                        'api_key': api_key},
                                       {'_id': True}):
            raise HTTPException(HTTPStatus.FORBIDDEN)
        request.session['dicts'] = [dictionary, *user_dicts[:5]]
    return db


@router.get('/dictionaries', response_model=Dictionaries)
async def dictionaries(
        db=Depends(get_db),
        api_key=_APIKEY_HEADER,
):
    objs = await db.dicts.find(
        {'api_key': api_key},
        {'_id': True}
    ).to_list(None)
    names = [str(i['_id']) for i in objs]
    return dict(dictionaries=names)


@router.get('/about/{dictionary}', response_model=Dictionary)
async def about(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
):
    doc = await db.dicts.find_one(
        {'_id': ObjectId(dictionary)},
        {'_id': False, '_entries': False})
    if not doc:
        raise HTTPException(HTTPStatus.NOT_FOUND)
    doc.update(doc.pop('meta'))
    return doc


@router.get('/list/{dictionary}', response_model=List[Lemma])
async def list_dict(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
        offset: Optional[int] = _OFFSET_QUERY,
        limit: Optional[int] = _LIMIT_QUERY,
):
    entry_ids = await db.dicts.find_one(
        {'_id': ObjectId(dictionary)},
        {'_entries': {'$slice': [offset, limit]}, '_id': False, 'meta': False})
    entries = await db.entry.aggregate([
        {'$match': {'_id': {'$in': entry_ids['_entries']}}},
        {'$set': {'id': '$_id'}},
        {'$project': dict(zip(Lemma.__fields__.keys(),
                              itertools.repeat(True)))},
    ]).to_list(None)
    return jsonable_encoder(entries, custom_encoder={ObjectId: str})


@router.get('/lemma/{dictionary}/{headword}', response_model=List[Lemma])
async def list_lemma(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
        headword: str = Path(...),
        partOfSpeech: Optional[PartOfSpeech] = Query(None),
        offset: Optional[int] = _OFFSET_QUERY,
        limit: Optional[int] = _LIMIT_QUERY,
        inflected: Optional[bool] = Query(False),   # TODO: what about this?
):
    pos_cond = {'partOfSpeech': partOfSpeech} if partOfSpeech else {}
    entries = await db.entry.aggregate([
        {'$match': {'_dict_id': ObjectId(dictionary),
                    'lemma': headword,
                    **pos_cond}},
        {'$skip': offset},
        {'$limit': limit},
        {'$set': {'id': '$_id'}},
        {'$project': dict(zip(Lemma.__fields__.keys(),
                              itertools.repeat(True)))},
    ]).to_list(None)
    return jsonable_encoder(entries, custom_encoder={ObjectId: str})


@router.get('/json/{dictionary}/{entry_id}')
async def entry_json(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
        entry_id: str = Path(...),
):
    entry = await _get_entry(db, dictionary, entry_id)
    return Response(entry_to_jsonld(entry),
                    headers={'Link': '</context.jsonld>; '
                                     'rel="http://www.w3.org/ns/json-ld#context"; '
                                     'type="application/ld+json"'},
                    media_type='application/ld+json')


@router.get('/tei/{dictionary}/{entry_id}')
async def entry_tei(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
        entry_id: str = Path(...),
):
    entry = await _get_entry(db, dictionary, entry_id)
    return Response(entry_to_tei(entry),
                    media_type='text/xml')


@router.get('/ontolex/{dictionary}/{entry_id}')
async def entry_ontolex(
        db=Depends(_get_db_verify_api_key),
        dictionary: str = _DICT_PATH,
        entry_id: str = Path(...),
):
    entry = await _get_entry(db, dictionary, entry_id)
    return Response(entry_to_ontolex(entry),
                    media_type='text/turtle')


async def _get_entry(db, dictionary, entry_id,):
    entry = await db.entry.find_one(
        {'_dict_id': ObjectId(dictionary), '_id': ObjectId(entry_id)},
        {'_dict_id': False, 'lemma': False})
    return entry


@router.get('/context.jsonld', include_in_schema=False)
def jsonld_context():
    return ORJSONResponse(JSONLD_CONTEXT,
                          media_type='application/ld+json')
