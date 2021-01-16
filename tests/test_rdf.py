import json

from app.rdf import entry_to_jsonld, entry_to_tei, entry_to_turtle, file_to_obj


def test_file_to_obj(example_file):
    obj = file_to_obj(example_file)
    assert 'entries' in obj


def assert_tei(text):
    assert '<form type="lemma">' in text


def assert_turtle(text):
    assert '@prefix ' in text
    assert 'lexinfo:partOfSpeech lexinfo:' in text
    assert 'file:///' not in text, 'URIRefs should not be file-based'


def assert_jsonld(obj):
    assert '@context' in obj
    assert 'partOfSpeech' in obj


def test_entry_to_tei(entry_obj):
    text = entry_to_tei(entry_obj)
    assert_tei(text)


def test_entry_to_turtle(entry_obj):
    text = entry_to_turtle(entry_obj).decode()
    assert_turtle(text)


def test_entry_to_jsonld(entry_obj):
    obj = json.loads(entry_to_jsonld(entry_obj))
    assert_jsonld(obj)
