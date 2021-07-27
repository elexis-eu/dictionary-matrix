import pytest

from app.rdf import file_to_obj
from tests.conftest import TESTS_DIR

pytestmark = pytest.mark.asyncio


async def test_example_json():
    obj = file_to_obj(TESTS_DIR / "test_example.json")
    assert 2 == len(obj['entries'])


async def test_example_tei():
    obj = file_to_obj(TESTS_DIR / "test_example.xml")
    assert 1 == len(obj['entries'])


async def test_example_ontolex():
    obj = file_to_obj(TESTS_DIR / "test_example.ttl")
    assert 1 == len(obj['entries'])
