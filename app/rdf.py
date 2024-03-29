import html
import logging
import re
from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache, partial
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Optional, Union

import lxml.etree as ET
import orjson
from rdflib import Graph
from rdflib.namespace import DC, DCTERMS, RDF, SKOS, XMLNS

from .models import Entry, JsonDictionary, LexicalEntry
from .settings import settings

log = logging.getLogger(__name__)

ONTOLEX = 'http://www.w3.org/ns/lemon/ontolex#'
LIME = 'http://www.w3.org/ns/lemon/lime#'
LEXINFO = 'http://www.lexinfo.net/ontology/3.0/lexinfo#'
TEI = 'http://www.tei-c.org/ns/1.0'

_RDF_IMPORT_BASE = 'elexis:dict'  # Our every imported Turtle dict's namespace
_RDF_EXPORT_BASE = 'elexis:.#'

_tei_to_ontolex = ET.XSLT(
    ET.parse(str(Path(__file__).resolve().parent / 'TEI2Ontolex.xsl')),
    access_control=ET.XSLTAccessControl.DENY_ALL)

_parse_xml = partial(ET.parse, parser=ET.XMLParser(recover=True,
                                                   remove_pis=True,
                                                   collect_ids=False,
                                                   remove_comments=True,
                                                   resolve_entities=True,
                                                   remove_blank_text=True))


def removeprefix(string: str, prefix: str = _RDF_EXPORT_BASE) -> str:
    return (string[len(prefix):]
            if string and string.startswith(prefix) else
            string)


def file_to_obj(filename: Union[str, Path], language: str = None):
    assert Path(filename).is_file(), filename
    filename = str(Path(filename))

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
        graph.parse(filename, format='turtle', publicID=_RDF_IMPORT_BASE)
        xml = graph.serialize(format='pretty-xml').encode('utf-8')
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
    for entry in obj['entries']:
        # Convert POS from Lexinfo to UD. Strip its JSON-LD naemspace prefix.
        entry['partOfSpeech'] = lexinfo_pos_to_ud(entry['partOfSpeech'].split(':')[-1])
        # Strip type namespace base URI
        # FIXME: This is fragile and based solely on our JSON-LD entry export
        entry['@type'] = entry['@type'].split('#')[-1]

        # Make sure senses are available
        if 'senses' not in entry:
            entry['senses'] = []

        # Key canonicalForm etc. by language if not already
        lang = entry.get('language', obj['meta']['sourceLanguage'])
        for rep, s in entry.get('canonicalForm', {}).items():
            if isinstance(s, str):
                entry['canonicalForm'][rep] = {lang: [s]}
        for sense in entry['senses']:
            if isinstance(sense['definition'], str):
                sense['definition'] = {lang: sense['definition']}

        if 'lemma' not in entry:
            entry['lemma'] = entry['canonicalForm']['writtenRep'][lang][0]

    obj = JsonDictionary(**obj).dict(exclude_none=True, exclude_unset=True)
    return obj


def _ontolex_etree_to_dict(root: ET.ElementBase, language: str = None) -> dict:  # noqa: C901
    RDF_RESOURCE = f'{{{RDF}}}resource'
    RDF_ABOUT = f'{{{RDF}}}about'
    RDF_ID = f'{{{RDF}}}ID'
    XMLNS_ID = f'{{{XMLNS}}}id'
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

    def rdf_id(el: ET.ElementBase) -> str:
        id = (el.attrib.get(RDF_ABOUT)
              or el.attrib.get(RDF_ID)
              or el.attrib.get(XMLNS_ID))
        id = removeprefix(id, _RDF_IMPORT_BASE + '#')
        return id

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

    def copy_with_lemma(entry_obj: dict, headword: str) -> dict:
        entry_obj = deepcopy(entry_obj)
        entry_obj['lemma'] = headword
        # Set writtenRep to the current lemma ONLY as this
        # (canonicalForm.writtenRep) is the main way the entry reports
        # (exports) its lemma (see entry_to_* below).
        entry_obj['canonicalForm']['writtenRep'][lexicon_lang] = [headword]
        return entry_obj

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
            'origin_id': rdf_id(entry_el),
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
                f"'Need exactly one partOfSpeech for entry #{entry_i}: {writtenRep}, have {pos}"
            entry_obj['partOfSpeech'] = \
                lexinfo_pos_to_ud(strip_ns(pos[0].attrib[RDF_RESOURCE]))

            # Senses
            for sense_el in get_sense(entry_el):
                sense_id = rdf_id(sense_el)
                sense_obj: dict = {
                    'id': sense_id,
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
                    sense_obj['definition'][lang] = '; '.join(defs)

                sense_obj = remove_empty_keys(sense_obj)
                if sense_obj and (sense_obj.get('definition') or
                                  sense_obj.get('reference')):
                    entry_obj['senses'].append(sense_obj)

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
                copy_with_lemma(entry_obj, headword)
                for headword in entry_obj['canonicalForm']['writtenRep'][lexicon_lang]
            ])
        except Exception as e:
            if len(errors) < 50:
                errors.append(str(e))
        else:
            if settings.DEBUG:
                _ = Entry(**entry_obj)

    if not lexicon_obj['entries']:
        raise ValueError('No valid entries found. Errors:' + '\n'.join(errors or []))

    log.debug(f'Found {len(lexicon_obj["entries"])} valid (of {entry_i + 1}) entries')

    # Set languages
    lexicon_obj['meta']['sourceLanguage'] = lexicon_lang
    targetLanguages.discard(lexicon_lang)
    if targetLanguages:
        lexicon_obj['meta']['targetLanguage'] = list(targetLanguages)

    return lexicon_obj


def entry_to_tei(entry: dict, *, original_ids=False, skip_elexis_xmlns=False) -> str:
    # TODO: rewrite as tei.tpl
    # TODO: escape HTML
    pos = entry['partOfSpeech']
    lemmas = [f'<orth xml:lang="{lang}">{html.escape(value, quote=False)}</orth>'
              for lang, values in entry['canonicalForm']['writtenRep'].items()
              for value in values]

    def defns(sense):
        return ''.join(f'<def xml:lang="{lang}">{html.escape(value, quote=False)}</def>'
                       for lang, value in sense['definition'].items())
    senses = [
        '<sense n="{i}"{id}>{text}</sense>'.format(
            i=i, text=defns(sense),
            id=f' xml:id="{sense["id"]}"' if sense.get('id') else '')
        for i, sense in enumerate(entry['senses'], 1)
    ]
    entry_id = original_ids and entry.get('_origin_id') or entry['_id']
    origin_id = entry.get('origin_id', '')
    origin_id_str = ''
    if origin_id:
        origin_id_str = f' elexis:origin_id="{origin_id}"'
        if not skip_elexis_xmlns:
            origin_id_str = f' xmlns:elexis="http://matrix.elex.is/" {origin_id_str}'
    NEWLINE = '\n'
    xml = f'''\
<entry xml:id="{entry_id}"{origin_id_str}>
<form type="lemma">{''.join(lemmas)}</form>
<gramGrp><pos norm="{pos}">{pos}</pos></gramGrp>
{NEWLINE.join(senses)}
</entry>
'''
    return xml


def entry_to_turtle(entry: dict) -> str:
    graph = Graph()
    graph.parse(data=entry_to_jsonld(entry, prefix_ids=True),
                format='json-ld')
    return graph.serialize(format='turtle')


# TODO: dcterms needed?
JSONLD_CONTEXT = {
    'ontolex': ONTOLEX,
    'lexinfo': LEXINFO,
    'lime': LIME,
    'skos': str(SKOS),
    'origin_id': '#origin_id',
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


def entry_to_jsonld(entry: dict, *, prefix_ids=False) -> bytes:
    obj = deepcopy(entry)
    # TODO: Make @context a href
    obj['@context'] = JSONLD_CONTEXT
    add_entry_sense_ids(obj)
    obj['@type'] = ONTOLEX + obj.pop('type')
    obj['partOfSpeech'] = 'lexinfo:' + ud_to_lexinfo_pos(obj['partOfSpeech'])
    if prefix_ids:
        obj['@id'] = _RDF_EXPORT_BASE + obj['@id']
        for sense in obj['senses']:
            sense['@id'] = _RDF_EXPORT_BASE + sense['@id']
    return orjson.dumps(obj, option=orjson.OPT_INDENT_2 * bool(settings.DEBUG))


def add_entry_sense_ids(entry, id_key='@id'):
    """
    Entry senses are assigned ids as in the input RDF,
    or '{entry_id}-{n}' when none. Modifies the dict in place.
    """
    entry[id_key] = id = str(entry.pop("_id"))
    for i, sense in enumerate(entry['senses']):
        sense[id_key] = sense.pop('id', f'{id}-{i}')
    return entry


def export_for_naisc(entries: Iterable) -> str:
    graph = Graph()
    for entry in entries:
        graph.parse(data=entry_to_jsonld(entry, prefix_ids=True),
                    format='json-ld')
    return graph.serialize(format='turtle')


def export_to_tei(dict_obj):
    meta = dict_obj.get('meta', {})
    yield f'''\
<?xml version="1.0" encoding="UTF-8"?>
<?xml-model href="http://www.tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng"
            schematypens="http://relaxng.org/ns/structure/1.0" type="application/xml"?>
<!-- Should validate with `xmllint -relaxng $model-href $file` -->
<TEI xmlns="http://www.tei-c.org/ns/1.0" xmlns:elexis="http://matrix.elex.is/">
    <teiHeader>
        <fileDesc>
            <titleStmt>
                <title>{meta.get('title', '')}</title>
                <author>{meta.get('author', '')}</author>
            </titleStmt>
            <publicationStmt>
                <availability>
                    <licence target="{meta.get('license', '')}">{meta.get('license', '')}</licence>
                </availability>
                <publisher>{meta.get('publisher', '')}</publisher>
            </publicationStmt>
        </fileDesc>
    </teiHeader>
    <text>
        <body>
'''
    for entry in dict_obj['entries']:
        yield entry_to_tei(entry, original_ids=True, skip_elexis_xmlns=True)
    yield '''\
</body></text></TEI>
'''


# Generated with:
#     isoquery --iso 639-3 | cut -d$'\t' -f1,4 | grep -P '\t..' | sed -r 's/\t(..)/="\1",/'
_ISO639_3TO1 = dict(
    aar="aa", abk="ab", afr="af", aka="ak", amh="am", ara="ar", arg="an", asm="as", ava="av",
    ave="ae", aym="ay", aze="az", bak="ba", bam="bm", bel="be", ben="bn", bis="bi", bod="bo",
    bos="bs", bre="br", bul="bg", cat="ca", ces="cs", cha="ch", che="ce", chu="cu", chv="cv",
    cor="kw", cos="co", cre="cr", cym="cy", dan="da", deu="de", div="dv", dzo="dz", ell="el",
    eng="en", epo="eo", est="et", eus="eu", ewe="ee", fao="fo", fas="fa", fij="fj", fin="fi",
    fra="fr", fry="fy", ful="ff", gla="gd", gle="ga", glg="gl", glv="gv", grn="gn", guj="gu",
    hat="ht", hau="ha", hbs="sh", heb="he", her="hz", hin="hi", hmo="ho", hrv="hr", hun="hu",
    hye="hy", ibo="ig", ido="io", iii="ii", iku="iu", ile="ie", ina="ia", ind="id", ipk="ik",
    isl="is", ita="it", jav="jv", jpn="ja", kal="kl", kan="kn", kas="ks", kat="ka", kau="kr",
    kaz="kk", khm="km", kik="ki", kin="rw", kir="ky", kom="kv", kon="kg", kor="ko", kua="kj",
    kur="ku", lao="lo", lat="la", lav="lv", lim="li", lin="ln", lit="lt", ltz="lb", lub="lu",
    lug="lg", mah="mh", mal="ml", mar="mr", mkd="mk", mlg="mg", mlt="mt", mon="mn", mri="mi",
    msa="ms", mya="my", nau="na", nav="nv", nbl="nr", nde="nd", ndo="ng", nep="ne", nld="nl",
    nno="nn", nob="nb", nor="no", nya="ny", oci="oc", oji="oj", ori="or", orm="om", oss="os",
    pan="pa", pli="pi", pol="pl", por="pt", pus="ps", que="qu", roh="rm", ron="ro", run="rn",
    rus="ru", sag="sg", san="sa", sin="si", slk="sk", slv="sl", sme="se", smo="sm", sna="sn",
    snd="sd", som="so", sot="st", spa="es", sqi="sq", srd="sc", srp="sr", ssw="ss", sun="su",
    swa="sw", swe="sv", tah="ty", tam="ta", tat="tt", tel="te", tgk="tg", tgl="tl", tha="th",
    tir="ti", ton="to", tsn="tn", tso="ts", tuk="tk", tur="tr", twi="tw", uig="ug", ukr="uk",
    urd="ur", uzb="uz", ven="ve", vie="vi", vol="vo", wln="wa", wol="wo", xho="xh", yid="yi",
    yor="yo", zha="za", zho="zh", zul="zu",
)


def to_iso639(lang):
    if isinstance(lang, str) and '-' in lang:
        # Handle IETF/BCP47 language tags, such as "en-US",
        # "ar-aeb" (Arabic as spoken in Tunis)
        lang = lang[:lang.index('-')]
    return _ISO639_3TO1.get(lang, lang)


# UniversalDependencies to Lexinfo POS and vice versa
# https://universaldependencies.org/u/pos/
_UD2LEXINFO = {
    'ADJ': 'adjective',
    'ADP': 'adposition',
    'ADV': 'adverb',
    'AUX': 'auxiliary',
    'CCONJ': 'coordinatingConjunction',
    'DET': 'determiner',
    'INTJ': 'interjection',
    'NOUN': 'commonNoun',
    'NUM': 'numeral',
    'PART': 'particle',
    'PRON': 'pronoun',
    'PROPN': 'properNoun',
    'PUNCT': 'punctuation',
    'SCONJ': 'subordinatingConjunction',
    'SYM': 'symbol',
    'VERB': 'verb',
    'X': 'other',
}

_LEXINFO2UD = {v: k for k, v in _UD2LEXINFO.items()}


def lexinfo_pos_to_ud(pos):
    return _LEXINFO2UD.get(pos, pos)


def ud_to_lexinfo_pos(ud_pos):
    return _UD2LEXINFO.get(ud_pos, ud_pos)
