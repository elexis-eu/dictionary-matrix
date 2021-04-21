import sys
from typing import List, Optional

from bson import ObjectId
from pydantic import AnyHttpUrl, FilePath, HttpUrl, root_validator, validator

from app.models import BaseModel, Genre, Language, ReleasePolicy, _AutoStrEnum

# Permit 'localhost' in tests, but not in production
Url = AnyHttpUrl if 'pytest' in sys.modules else HttpUrl


class JobStatus(_AutoStrEnum):
    SCHEDULED, ERROR, DONE = _AutoStrEnum._auto(3)


class _ImportMeta(BaseModel):
    release: ReleasePolicy
    sourceLanguage: Optional[Language]
    genre: Optional[List[Genre]]
    api_key: str


class ImportJob(BaseModel):
    url: Optional[Url]  # type: ignore
    file: Optional[FilePath]
    state: JobStatus
    meta: _ImportMeta
    dict_id: Optional[ObjectId]

    @validator('url', 'file')
    def cast_to_str(cls, v):
        return str(v) if v else None

    @root_validator
    def check_valid(cls, values):
        assert values['url'] or values['file']
        return values
