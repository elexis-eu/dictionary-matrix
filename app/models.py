from __future__ import annotations

from enum import Enum, auto
from typing import Dict, List, Optional, Union

import orjson
from pydantic import (
    BaseModel as _BaseModel,
    Field, HttpUrl, conlist, constr, root_validator, validator,
)


class _AutoStrEnum(str, Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name

    @staticmethod
    def _auto(n):
        return [auto() for _ in range(n)]

    @classmethod
    def values(cls):
        return [i.value for i in cls]


class BaseModel(_BaseModel):
    """Our Pydantic model base."""
    class Config:
        # Use enum values rather than objects for model.dict()
        use_enum_values = True
        # Perform validation on assignment to attributes
        validate_assignment = True
        # Populate aliased fields either by attribute name or by alias
        allow_population_by_field_name = True
        # Override for decoding JSON
        json_loads = orjson.loads
        # Allow declaring e.g. bson.ObectId
        arbitrary_types_allowed = True


class RdfFormats(_AutoStrEnum):
    TEI = 'tei'
    JSON = 'json'
    ONTOLEX = 'ontolex'

    @classmethod
    def sort_key(cls):
        return [cls.JSON, cls.TEI, cls.ONTOLEX].index


class ReleasePolicy(_AutoStrEnum):
    PUBLIC, NONCOMMERCIAL, RESEARCH, PRIVATE = _AutoStrEnum._auto(4)


class Genre(_AutoStrEnum):
    # TODO: Find a way to document these
    gen = auto()  # [General dictionaries] are dictionaries that document contemporary vocabulary and are intended for everyday reference by native and fluent speakers.  # noqa: E501
    lrn = auto()  # [Learners' dictionaries] are intended for people who are learning the language as a second language.  # noqa: E501
    ety = auto()  # [Etymological dictionaries] are dictionaries that explain the origins of words.
    spe = auto()  # [Dictionaries on special topics] are dictionaries that focus on a specific subset of the vocabulary (such as new words or phrasal verbs) or which focus on a specific dialect or variant of the language.  # noqa: E501
    his = auto()  # [Historical dictionaries] are dictionaries that document previous historical states of the language.  # noqa: E501
    ort = auto()  # [Spelling dictionaries] codify the correct spelling and other aspects of the orthography of words.  # noqa: E501
    trm = auto()  # [Terminological dictionaries] describe the vocabulary of specialized domains such as biology, mathematics or economics.  # noqa: E501
    # por = auto()  # [Portals and aggregators] are websites that provide access to more than one dictionary and allow you to search them all at once.  # noqa: E501


class PartOfSpeech(_AutoStrEnum):
    """
    From: https://universaldependencies.org/u/pos/
    """
    (
        ADJ,  # adjective
        ADP,  # adposition
        ADV,  # adverb
        AUX,  # auxiliary
        CCONJ,  # coordinating conjunction
        DET,  # determiner
        INTJ,  # interjection
        NOUN,  # noun
        NUM,  # numeral
        PART,  # particle
        PRON,  # pronoun
        PROPN,  # proper noun
        PUNCT,  # punctuation
        SCONJ,  # subordinating conjunction
        SYM,  # symbol
        VERB,  # verb
        X,  # other
    ) = _AutoStrEnum._auto(17)


class Dictionaries(BaseModel):
    dictionaries: List[str]


class Language(constr(regex=r'[a-z]{2,3}')):  # type: ignore
    """ISO 639 2-alpha or 3-alpha language string"""


class Dictionary(BaseModel):
    release: ReleasePolicy
    sourceLanguage: Language
    targetLanguage: Optional[List[Language]]
    genre: Optional[List[Genre]]
    license: Optional[Union[HttpUrl, str]]
    title: Optional[str]
    creator: Optional[Union[List, str]]
    publisher: Optional[Union[List, str]]

    @validator('genre', 'targetLanguage', pre=True)
    def ensure_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v


class Lemma(BaseModel):
    lemma: str
    id: str
    partOfSpeech: PartOfSpeech
    language: Language
    formats: Optional[List[RdfFormats]]


_LangValue = Dict[Language, str]
_LangValues = Dict[Language, List[str]]


class _CanonicalForm(BaseModel):
    writtenRep: Optional[_LangValues]
    phoneticRep: Optional[_LangValues]

    @root_validator
    def check_valid(cls, values):
        assert values['writtenRep'] or values['phoneticRep']
        return values


class _Sense(BaseModel):
    id: Optional[str]
    definition: Optional[_LangValue]
    reference: Optional[List[HttpUrl]]

    @root_validator
    def check_valid(cls, values):
        assert values['definition'] or values['reference'], \
            'sense requires definition and/or reference'
        return values


class LexicalEntry(_AutoStrEnum):
    LexicalEntry, Word, Affix, MultiWordExpression = _AutoStrEnum._auto(4)


class Entry(BaseModel):
    context: Optional[Union[Dict, HttpUrl]] = Field(alias='@context')
    type: LexicalEntry = Field(alias='@type')
    id: Optional[str] = Field(alias='@id')

    lemma: Optional[str]
    canonicalForm: _CanonicalForm
    partOfSpeech: PartOfSpeech
    senses: conlist(_Sense, min_items=0)  # type: ignore

    language: Optional[Language]
    otherForm: Optional[List[_CanonicalForm]]
    morphologicalPattern: Optional[List[str]]
    etymology: Optional[List[str]]
    usage: Optional[List[str]]

    # TODO: Private header last-modified

    @root_validator
    def check_minimal_requirements(cls, values):
        assert values['canonicalForm'].writtenRep
        return values


class JsonDictionary(BaseModel):
    meta: Dictionary
    entries: conlist(Entry, min_items=1)  # type: ignore
