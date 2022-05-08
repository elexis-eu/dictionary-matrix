import os
import tempfile
from pathlib import Path
from typing import Optional

from pydantic import AnyHttpUrl, AnyUrl, BaseSettings, DirectoryPath, FilePath, validator

_APP_PATH = Path(__file__).resolve().parent

__pdoc__ = {'_Settings': True}


class _Settings(BaseSettings):
    DEBUG: bool = False

    APP_TITLE: str = 'ELEXIS Dictionary Matrix'
    APP_DESCRIPTION: str = '''A lexicographic web API that implements
<a href="https://elexis-eu.github.io/elexis-rest/">ELEXIS Protocol
for accessing dictionaries (1.2)</a>
and <a href="https://elexis-eu.github.io/elexis-rest/linking.html">ELEXIS Protocol
for Dictionary Linking (1.0)</a>.
'''

    # Base URL where this app is hosted. Used for linking.
    # TODO: test this is set correctly
    SITEURL: AnyHttpUrl = 'http://localhost:8000'  # type: ignore

    SESSION_COOKIE_SECRET_KEY: str = 'changeme secret'
    SESSION_COOKIE_MAX_AGE: int = 24 * 60 * 60

    MONGODB_CONNECTION_STRING: AnyUrl = 'mongodb://localhost:27017/?connectTimeoutMS=3000'  # type: ignore  # noqa: E501
    MONGODB_DATABASE: str = 'dictionary_matrix'

    UPLOAD_PATH: DirectoryPath = str(Path(tempfile.gettempdir()) / "dictionary-matrix-uploads")  # type: ignore  # noqa: E501
    UPLOAD_N_WORKERS: int = 2
    UPLOAD_TIMEOUT_SECONDS: float = 60 * 10
    UPLOAD_REMOVE_ON_SUCCESS: bool = True
    UPLOAD_REMOVE_ON_FAILURE: bool = True

    API_IMPORT_N_WORKERS: int = 2
    API_IMPORT_TIMEOUT_SECONDS: float = 60 * 60 * 3

    LOGGING_CONFIG_FILE: FilePath = str(_APP_PATH / 'logging.dictConfig.json')  # type: ignore
    LOG_LEVEL: Optional[str] = None
    LOG_FILE: Optional[FilePath] = None

    DEPLOYMENT_CONFIG_FILE: FilePath = str(_APP_PATH / 'gunicorn.py.conf')  # type: ignore

    LINKING_N_WORKERS: int = 2
    # Require URL to Naisc with ELEXIS REST API with support for X-API-Key header.
    # See: https://github.com/insight-centre/naisc/issues/7
    LINKING_NAISC_URL: Optional[AnyHttpUrl] = None
    LINKING_BABELNET_URL: AnyHttpUrl = 'https://babelnet.io/v5/'    # type: ignore
    LINKING_NAISC_EXECUTABLE: Optional[FilePath] = None

    class Config:
        env_file = '.env'
        allow_mutation = False

    @validator('*')
    def ensure_urls_and_paths_are_str(cls, v: object):
        return str(v) if isinstance(v, (AnyUrl, Path)) else v


settings = _Settings()
"""
Global project settings namespace.
Import and reference values from this object.
"""

os.makedirs(settings.UPLOAD_PATH, exist_ok=True)
