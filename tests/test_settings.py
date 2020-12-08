from app.settings import _Settings, settings


def test_defaults():
    assert 'ELEXIS' in settings.APP_TITLE
    assert 'mongodb://' in settings.MONGODB_CONNECTION_STRING


def test_from_environ(monkeypatch):
    monkeypatch.setenv('APP_TITLE', 'foo')
    assert _Settings().APP_TITLE == 'foo'
