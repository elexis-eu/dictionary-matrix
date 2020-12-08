import tempfile
from pathlib import Path
from typing import Optional

from pydantic import BaseSettings


_APP_PATH = Path(__file__).resolve().parent

__pdoc__ = {'_Settings': True}


class _Settings(BaseSettings):
    DEBUG: bool = False

    APP_TITLE: str = 'ELEXIS Dictionary Matrix'
    APP_DESCRIPTION: str = '...'  # TODO

    SESSION_COOKIE_SECRET_KEY: str = 'changeme secret'
    SESSION_COOKIE_MAX_AGE: int = 24 * 60 * 60

    MONGODB_CONNECTION_STRING: str = 'mongodb://localhost:27017/?connectTimeoutMS=3000'
    MONGODB_DATABASE: str = 'dictionary_matrix'

    UPLOAD_PATH: str = str(Path(tempfile.gettempdir()) / "dictionary-matrix-uploads")
    UPLOAD_N_WORKERS: int = 2
    UPLOAD_TIMEOUT_SECONDS: float = 60 * 10
    UPLOAD_REMOVE_ON_SUCCESS: bool = True
    UPLOAD_REMOVE_ON_FAILURE: bool = True

    LOGGING_CONFIG_FILE: str = str(_APP_PATH / 'logging.dictConfig.json')
    LOG_LEVEL: Optional[str] = None
    LOG_FILE: Optional[str] = None

    DEPLOYMENT_CONFIG_FILE: str = str(_APP_PATH / 'gunicorn.py.conf')

    class Config:
        env_file = '.env'
        allow_mutation = False


settings = _Settings()
"""
Global project settings namespace.
Import and reference values from this object.
"""
