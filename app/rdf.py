import logging
import re
from collections import Counter, defaultdict
from functools import lru_cache, partial
from io import BytesIO
from pathlib import Path
from typing import List, Optional

import lxml.etree as ET
import orjson
from rdflib import Graph
from rdflib.namespace import DC, DCTERMS, RDF, SKOS, XMLNS

from .models import Entry, JsonDictionary, LexicalEntry
from .models_helpers import lexinfo_pos_to_ud, to_iso639, ud_to_lexinfo_pos
from .settings import settings

log = logging.getLogger(__name__)

ONTOLEX = 'http://www.w3.org/ns/lemon/ontolex#'
LIME = 'http://www.w3.org/ns/lemon/lime#'
LEXINFO = 'http://www.lexinfo.net/ontology/3.0/lexinfo#'
TEI = 'http://www.tei-c.org/ns/1.0'

_tei_to_ontolex = ET.XSLT(
    ET.parse(str(Path(__file__).resolve().parent / 'TEI2Ontolex.xsl')),
    access_control=ET.XSLTAccessControl.DENY_ALL)

_parse_xml = partial(ET.parse, parser=ET.XMLParser(recover=True,
                                                   remove_pis=True,
                                                   collect_ids=False,
                                                   remove_comments=True,
                                                   resolve_entities=True,
                                                   remove_blank_text=True))


def file_to_obj(filename: str, language: str = None):
    assert Path(filename).is_file(), filename

    with open(filename, encoding='utf-8') as f:
        head = f.read(1000)
    is_tei = re.search(r'<(\w+:)?TEI\b', head)
    is_turtle = re.search(r'^\s*@prefix\s', head)
    is_json = re.search(r'^\s*{\s*"', head)

    if is_tei:
        assert TEI in head, f'Missing required TEI xmlns ("{TEI}")'
        xml = _parse_xml(filename)
        xml = _tei_to_ontolex(xml)
        obj = _ontolex_etree_to_dict(xml, language)
        return obj

    if is_turtle:
        graph = Graph()
        graph.parse(filename, format='turtle', publicID='elexis:dict')
        xml = graph.serialize(format='pretty-xml')
        xml = _parse_xml(BytesIO(xml))
        obj = _ontolex_etree_to_dict(xml, language)
        return obj

    if is_json:
        obj = _from_json(filename)
        return obj

    # Ontolex/XML
    assert re.search(r'<(rdf:)?RDF\b', head)
    xml = _parse_xml(filename)
    obj = _ontolex_etree_to_dict(xml, language)
    return obj


def _from_json(filename):
    with open(filename, 'rb') as fd:
        obj = orjson.loads(fd.read())
    assert len(obj) == 1, "Expected one dictionary per JSON file"
    dict_id, obj = next(iter(obj.items()))
    obj = JsonDictionary(**obj).dict(exclude_none=True, exclude_unset=True)
    return obj


def _ontolex_etree_to_dict(root: ET.ElementBase, language: str = None) -> dict:  # noqa: C901
    RDF_RESOURCE = f'{{{RDF}}}resource'
    RDF_ABOUT = f'{{{RDF}}}about'
    XPath = partial(ET.XPath, smart_strings=False, regexp=False)

    @lru_cache(1)
    def rdf_about_map():
        log.debug('Building @rdf:about map')
        return {el.attrib[RDF_ABOUT]: el
                for el in root.xpath('.//*[@rdf:about]',
                                     namespaces={'rdf': str(RDF)},
                                     smart_strings=False)}

    def _maybe_resolve_resource(el: ET.ElementBase) -> ET.ElementBase:
        """
        If the matched element happens to point to a rdf:resource,
        look up (by matching rdf:about) and use that element instead.
        """
        if not el.text and RDF_RESOURCE in el.attrib and not len(el):
            resource = el.attrib[RDF_RESOURCE]
            assert not resource.startswith(LEXINFO)
            return rdf_about_map().get(resource, el)
        return el

    def resolve_resource(func):
        return lambda el: map(_maybe_resolve_resource, func(el))

    def xpath_local_name(tag):
        return XPath(f'.//*[local-name() = "{tag}"]')

    # We use namespace-less xpath matching. There's simply too many
    # valid namespaces to cover. For an example, see:
    # https://github.com/insight-centre/naisc/blob/fcdb370873/naisc-core/src/main/java/org/insightcentre/uld/naisc/blocking/OntoLex.java  # noqa: E501
    get_lexicon = resolve_resource(xpath_local_name('Lexicon'))
    get_language = resolve_resource(xpath_local_name('language'))
    get_dublin_core = resolve_resource(XPath(f'''
        .//*[contains("|{DC}|{DCTERMS}|",
                      concat("|", namespace-uri(), "|"))]
    '''))
    # TODO: add check for canonicalForm.writtenRep, partOfSpeech, definition
    # https://stackoverflow.com/questions/105613/can-xpath-return-only-nodes-that-have-a-child-of-x
    ENTRY_TAGS = LexicalEntry.values()
    get_entry = resolve_resource(XPath(f'''
        .//*[contains("|{'|'.join(ENTRY_TAGS)}|",
                      concat("|", local-name(), "|"))]
    '''))
    get_canonicalForm = resolve_resource(xpath_local_name('canonicalForm'))
    get_otherForm = resolve_resource(xpath_local_name('otherForm'))
    get_writtenRep = resolve_resource(xpath_local_name('writtenRep'))
    get_phoneticRep = resolve_resource(xpath_local_name('phoneticRep'))
    get_partOfSpeech = xpath_local_name('partOfSpeech')  # Don't auto_resolve_resource!
    get_morphologicalPattern = resolve_resource(xpath_local_name('morphologicalPattern'))
    get_sense = resolve_resource(xpath_local_name('sense'))
    get_definition = resolve_resource(xpath_local_name('definition'))
    get_reference = resolve_resource(xpath_local_name('reference'))
    get_etymology = resolve_resource(xpath_local_name('etymology'))
    get_usage = resolve_resource(xpath_local_name('usage'))

    def strip_ns(tag: str) -> str:
        return (tag[tag.rindex('}') + 1:] if '}' in tag else  # ElementTree/lxml tag
                tag[tag.rindex('#') + 1:] if '#' in tag else  # Namespace URI
                tag)

    def text_content(el: ET.ElementBase) -> str:
        text = ET.tostring(el, encoding=str, method='text').strip()
        return re.sub(r'\s{2,}', ' ', text)

    def xml_lang(
            el: ET.ElementBase, *,
            _get_lang=XPath('ancestor-or-self::*[@xml:lang][1]/@xml:lang',
                            namespaces={'xml': str(XMLNS)})) -> str:
        lang = next(iter(_get_lang(el)), None)
        if lang:
            lang = to_iso639(lang)
            targetLanguages.add(lang)
        return lang

    def infer_lang_from_entries() -> Optional[str]:
        counter: Counter = Counter(root.xpath('.//@xml:lang',
                                              namespaces={'xml': str(XMLNS)},
                                              smart_strings=False))
        return counter.most_common(1)[0][0] if counter else None

    def is_entry_descendant(
            el: ET.ElementBase, *,
            _get_entry=XPath(f'''
                ancestor-or-self::*[contains("|{'|'.join(ENTRY_TAGS)}|",
                                             concat("|", local-name(), "|"))]
            ''')) -> bool:
        return next(iter(_get_entry(el)), None) is not None

    def remove_empty_keys(obj):
        """Remove keys with "empty" values, recursively."""
        if isinstance(obj, dict):
            return {k: v for k, v in ((k, remove_empty_keys(v))
                                      for k, v in obj.items()) if v}
        if isinstance(obj, list):
            return [v for v in (remove_empty_keys(v) for v in obj) if v]
        return obj

    targetLanguages = set()
    errors: List[str] = []
    lexicon_obj: dict = {
        'entries': [],
        'meta': {},
    }
    lexicon_el = next(iter(get_lexicon(root)), root)

    # Lexicon meta data
    for el in get_dublin_core(lexicon_el):
        if is_entry_descendant(el):
            break
        tag = strip_ns(el.tag)
        value = text_content(el) or el.attrib.get(RDF_RESOURCE)
        if value:
            lexicon_obj['meta'][tag] = value

    # Lexicon language
    lexicon_lang = to_iso639(language) or xml_lang(lexicon_el)
    if not lexicon_lang:
        for lang_el in get_language(lexicon_el):
            if not is_entry_descendant(lang_el):
                lexicon_lang = to_iso639(text_content(lang_el))
                break
    if not lexicon_lang:
        lexicon_lang = infer_lang_from_entries()
    assert lexicon_lang, \
        'Need language for the dictionary. Either via lime:language, xml:lang, or language='

    # Get entries
    for entry_i, entry_el in enumerate(get_entry(lexicon_el)):
        entry_obj: dict = {
            'type': strip_ns(entry_el.tag),
            'canonicalForm': {
                'writtenRep': defaultdict(list),
                'phoneticRep': defaultdict(list),
            },
            'otherForm': [],
            'senses': [],
        }
        # Silently skip entries that fail
        try:
            # Set entry language
            lang = xml_lang(entry_el)
            if not lang:
                for lang_el in get_language(entry_el):
                    lang = to_iso639(text_content(lang_el))
                    break
            entry_lang = lang
            entry_obj['language'] = entry_lang or lexicon_lang

            # Canonical form / lemma / headword
            for form_el in get_canonicalForm(entry_el):
                for el in get_writtenRep(form_el):
                    lang = xml_lang(el) or entry_lang or lexicon_lang
                    entry_obj['canonicalForm']['writtenRep'][lang].append(text_content(el))
                for el in get_phoneticRep(form_el):
                    lang = xml_lang(el) or entry_lang or lexicon_lang
                    entry_obj['canonicalForm']['phoneticRep'][lang].append(text_content(el))

            writtenRep = dict(entry_obj['canonicalForm']['writtenRep'])
            assert writtenRep, \
                f"Missing canonicalForm.writtenRep for entry #{entry_i}"

            # Other forms
            for form_el in get_otherForm(entry_el):
                form_obj: dict = {
                    'writtenRep': defaultdict(list),
                    'phoneticRep': defaultdict(list),
                }
                entry_obj['otherForm'].append(form_obj)
                for el in get_writtenRep(form_el):
                    lang = xml_lang(el) or entry_lang or lexicon_lang
                    form_obj['writtenRep'][lang].append(text_content(el))
                for el in get_phoneticRep(form_el):
                    lang = xml_lang(el) or entry_lang or lexicon_lang
                    form_obj['phoneticRep'][lang].append(text_content(el))

            # Part-of-speech
            pos = list(get_partOfSpeech(entry_el))
            assert len(pos) == 1, \
                f"'Need exactly one partOfSpeech for entry #{entry_i}: {writtenRep}"
            entry_obj['partOfSpeech'] = \
                lexinfo_pos_to_ud(strip_ns(pos[0].attrib[RDF_RESOURCE]))

            # Senses
            for sense_el in get_sense(entry_el):
                sense_obj: dict = {
                    'definition': {},
                    'reference': [el.attrib[RDF_RESOURCE]
                                  for el in get_reference(sense_el)]
                }
                definitions = defaultdict(list)
                for el in get_definition(sense_el):
                    lang = xml_lang(el) or entry_lang or lexicon_lang
                    definitions[lang].append(text_content(el))
                # Join sense definitions in same language. Probably from sub-senses.
                for lang, defs in definitions.items():
                    sense_obj['definition'][lang] = ' '.join(defs)

                sense_obj = remove_empty_keys(sense_obj)
                if sense_obj:
                    entry_obj['senses'].append(sense_obj)
            assert entry_obj['senses'], f"Need sense for entry {writtenRep}"

            # Rest
            entry_obj['morphologicalPattern'] = \
                [text_content(el) for el in get_morphologicalPattern(entry_el)]
            entry_obj['etymology'] = \
                [text_content(el) for el in get_etymology(entry_el)]
            entry_obj['usage'] = \
                [text_content(el) for el in get_usage(entry_el)]

            # Construct an entry for each headword in the default language
            entry_obj = remove_empty_keys(entry_obj)
            lexicon_obj['entries'].extend([
                dict(entry_obj, lemma=headword)
                for headword in entry_obj['canonicalForm']['writtenRep'][lexicon_lang]
            ])
        except Exception as e:
            if len(errors) < 50:
                errors.append(str(e))
        else:
            if settings.DEBUG:
                _ = Entry(**entry_obj)

    if not lexicon_obj['entries']:
        raise ValueError('\n'.join(errors or ['No valid entries found']))

    # Set languages
    lexicon_obj['meta']['sourceLanguage'] = lexicon_lang
    targetLanguages.discard(lexicon_lang)
    if targetLanguages:
        lexicon_obj['meta']['targetLanguage'] = list(targetLanguages)

    return lexicon_obj


def entry_to_tei(entry: dict) -> str:
    # TODO: rewrite as tei.tpl
    # TODO: escape HTML
    pos = entry['partOfSpeech']
    lemmas = [f'<orth xml:lang="{lang}">{value}</orth>'
              for lang, values in entry['canonicalForm']['writtenRep'].items()
              for value in values]

    def defns(sense):
        return ''.join(f'<def xml:lang="{lang}">{value}</def>'
                       for lang, value in sense['definition'].items())
    senses = ['<sense n="{}">{}</sense>'.format(i, defns(sense))
              for i, sense in enumerate(entry['senses'], 1)]
    xml = f'''\
<entry xml:id="{entry['_id']}">
<form type="lemma">{''.join(lemmas)}</form>
<gramGrp><pos norm="{pos}">{pos}</pos></gramGrp>
{''.join(senses)}
</entry>
'''
    return xml


def entry_to_turtle(entry: dict) -> bytes:
    graph = Graph()
    graph.parse(data=entry_to_jsonld(entry), format='json-ld')
    return graph.serialize(format='turtle')


# TODO: dcterms needed?
JSONLD_CONTEXT = {
    'ontolex': ONTOLEX,
    'lexinfo': LEXINFO,
    'lime': LIME,
    'skos': str(SKOS),
    'canonicalForm': 'ontolex:canonicalForm',
    'otherForm': 'ontolex:otherForm',
    'writtenRep': {
        '@id': 'ontolex:writtenRep',
        '@container': '@language',
    },
    'phoneticRep': {
        '@id': 'ontolex:phoneticRep',
        '@container': '@language',
    },
    'senses': {
        '@id': 'ontolex:sense',
        '@container': '@set',
    },
    'definition': {
        '@id': 'skos:definition',
        '@container': '@language',
    },
    'reference': {
        '@id': 'ontolex:reference',
        '@type': '@id',
        '@container': '@set',
    },
    'partOfSpeech': {
        '@id': 'lexinfo:partOfSpeech',
        '@type': '@id',
    },
    'usage': 'ontolex:usage',
    'morphologicalPattern': 'ontolex:morphologicalPattern',
}


def entry_to_jsonld(entry: dict) -> bytes:
    obj = entry.copy()
    # TODO: Make @context a href
    obj['@context'] = JSONLD_CONTEXT
    obj['@id'] = f'elexis:{obj.pop("_id")}'
    obj['@type'] = ONTOLEX + obj.pop('type')
    obj['partOfSpeech'] = 'lexinfo:' + ud_to_lexinfo_pos(obj['partOfSpeech'])
    return orjson.dumps(obj, option=orjson.OPT_INDENT_2 * bool(settings.DEBUG))
