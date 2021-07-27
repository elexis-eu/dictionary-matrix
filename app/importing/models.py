import sys
from typing import List, Optional

from bson import ObjectId
from pydantic import AnyHttpUrl, Field, FilePath, HttpUrl, root_validator, validator

from app.models import BaseModel, Genre, Language, ReleasePolicy, _AutoStrEnum

# Permit 'localhost' in tests, but not in production
Url = AnyHttpUrl if 'pytest' in sys.modules else HttpUrl


class JobStatus(_AutoStrEnum):
    SCHEDULED, ERROR, DONE = _AutoStrEnum._auto(3)


class _ImportMeta(BaseModel):
    release: ReleasePolicy
    sourceLanguage: Optional[Language]
    genre: Optional[List[Genre]]


class FileImportJob(BaseModel):
    state: JobStatus
    api_key: str
    dict_id: Optional[ObjectId]
    url: Optional[Url]  # type: ignore
    file: Optional[FilePath]
    meta: _ImportMeta
    id: Optional[ObjectId] = Field(None, alias='_id')

    @validator('url', 'file')
    def cast_to_str(cls, v):
        return str(v) if v else None

    @root_validator
    def check_valid(cls, values):
        assert values['url'] or values['file']
        return values


class ApiImportJob(BaseModel):
    state: JobStatus
    api_key: str
    dict_id: Optional[ObjectId]
    url: Optional[Url]  # type: ignore
    remote_dict_id: str
    remote_api_key: Optional[str]
    id: Optional[ObjectId] = Field(None, alias='_id')

    @validator('url')
    def cast_to_str(cls, v):
        return str(v) if v else None

    @root_validator
    def check_valid(cls, values):
        assert values['url']
        return values
