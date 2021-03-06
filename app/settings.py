import tempfile
from pathlib import Path
from typing import Optional

from pydantic import AnyHttpUrl, AnyUrl, BaseSettings, DirectoryPath, FilePath, validator

_APP_PATH = Path(__file__).resolve().parent

__pdoc__ = {'_Settings': True}


class _Settings(BaseSettings):
    DEBUG: bool = False

    APP_TITLE: str = 'ELEXIS Dictionary Matrix'
    APP_DESCRIPTION: str = '...'  # TODO

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

    LOGGING_CONFIG_FILE: FilePath = str(_APP_PATH / 'logging.dictConfig.json')  # type: ignore
    LOG_LEVEL: Optional[str] = None
    LOG_FILE: Optional[FilePath] = None

    DEPLOYMENT_CONFIG_FILE: FilePath = str(_APP_PATH / 'gunicorn.py.conf')  # type: ignore

    LINKING_N_WORKERS: int = 2
    LINKING_NAISC_URL: AnyHttpUrl = 'http://localhost:8034/naisc/'  # type: ignore
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
