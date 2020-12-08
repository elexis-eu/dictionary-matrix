from app.rdf import file_to_obj


def test_file_to_obj(example_file):
    obj = file_to_obj(example_file)
    assert 'entries' in obj
