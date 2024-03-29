from typing import List, Optional

from bson import ObjectId
from pydantic import AnyHttpUrl, validator

from ..models import BaseModel, _AutoStrEnum


class LinkingJobStatus(_AutoStrEnum):
    PROCESSING, COMPLETED, FAILED = _AutoStrEnum._auto(3)


class LinkingSource(BaseModel):
    endpoint: Optional[AnyHttpUrl]
    id: str
    entries: Optional[List[str]]
    apiKey: Optional[str]

    @validator('endpoint')
    def cast_to_str(cls, v):
        return str(v) if v else None


class LinkingJob(BaseModel):
    source: LinkingSource
    target: LinkingSource
    config: Optional[dict]


class LinkingStatus(BaseModel):
    state: LinkingJobStatus = LinkingJobStatus.PROCESSING
    message: str = 'Still working ...'


class LinkingType(_AutoStrEnum):
    exact, broader, narrower, related = _AutoStrEnum._auto(4)


class SenseLink(BaseModel):
    source_sense: str
    target_sense: str
    type: LinkingType
    score: float


class LinkingOneResult(BaseModel):
    source_entry: str
    target_entry: str
    linking: List[SenseLink]


class LinkingJobPrivate(LinkingJob, LinkingStatus):
    remote_task_id: str = ''
    service_url: Optional[AnyHttpUrl] = None
    id: ObjectId
    result: Optional[List[LinkingOneResult]]

    @validator('service_url')
    def cast_to_str(cls, v):
        return v and str(v)
