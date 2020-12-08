from __future__ import annotations

from enum import Enum, auto
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, constr

from app.utils import _AutoEnum


class ISO639Language(constr(min_length=2, max_length=3)):  # type: ignore
    pass


class RdfFormats(str, Enum):
    TEI = 'tei'
    JSON = 'json'
    ONTOLEX = 'ontolex'


class ReleasePolicy(str, _AutoEnum):
    (
        PUBLIC,
        NONCOMMERCIAL,
        RESEARCH,
        PRIVATE
    ) = _AutoEnum._auto_range(4)


class Genre(str, _AutoEnum):
    # TODO: Find a way to document these
    gen = auto()  # [General dictionaries] are dictionaries that document contemporary vocabulary and are intended for everyday reference by native and fluent speakers.  # noqa: E501
    lrn = auto()  # [Learners' dictionaries] are intended for people who are learning the language as a second language.  # noqa: E501
    ety = auto()  # [Etymological dictionaries] are dictionaries that explain the origins of words.
    spe = auto()  # [Dictionaries on special topics] are dictionaries that focus on a specific subset of the vocabulary (such as new words or phrasal verbs) or which focus on a specific dialect or variant of the language.  # noqa: E501
    his = auto()  # [Historical dictionaries] are dictionaries that document previous historical states of the language.  # noqa: E501
    ort = auto()  # [Spelling dictionaries] codify the correct spelling and other aspects of the orthography of words.  # noqa: E501
    trm = auto()  # [Terminological dictionaries] describe the vocabulary of specialized domains such as biology, mathematics or economics.  # noqa: E501
    # por = auto()  # [Portals and aggregators] are websites that provide access to more than one dictionary and allow you to search them all at once.  # noqa: E501


class PartOfSpeech(str, _AutoEnum):
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
    ) = _AutoEnum._auto_range(17)


class Dictionaries(BaseModel):
    dictionaries: List[str]


class Dictionary(BaseModel):
    release: ReleasePolicy
    sourceLanguage: ISO639Language
    targetLanguage: Optional[List[ISO639Language]]
    genre: Optional[List[Genre]]
    license: Optional[HttpUrl]
    title: Optional[str]
    creator: Optional[Union[List, str]]
    publisher: Optional[Union[List, str]]


class Lemma(BaseModel):
    lemma: str
    id: str
    partOfSpeech: PartOfSpeech
    language: Optional[str]
    formats: Optional[List[RdfFormats]]


_LangValue = Dict[ISO639Language, str]
_LangValues = Dict[ISO639Language, List[str]]


class _CanonicalForm(BaseModel):
    writtenRep: Optional[_LangValues]
    phoneticRep: Optional[_LangValues]


class _Sense(BaseModel):
    definition: Optional[_LangValue]
    reference: Optional[List[HttpUrl]]
    # TODO: validate: `definition or reference`


class LexicalEntry(_AutoEnum):
    LexicalEntry = auto()
    Word = auto()
    Affix = auto()
    MultiWordExpression = auto()


class Entry(BaseModel):
    context: Optional[Union[Dict, HttpUrl]] = Field(alias='@context')
    type: LexicalEntry = Field(alias='@type')
    id: Optional[str] = Field(alias='@id')

    canonicalForm: _CanonicalForm
    partOfSpeech: PartOfSpeech
    senses: List[_Sense]

    language: Optional[str]
    otherForm: Optional[List[_CanonicalForm]]
    morphologicalPattern: Optional[List[str]]
    etymology: Optional[List[str]]
    usage: Optional[List[str]]

    # TODO: Private header last-modified


class JsonDictionary(BaseModel):
    meta: Dictionary
    entries: List[Entry]
