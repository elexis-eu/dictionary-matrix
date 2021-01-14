import json
import logging.config
import traceback
from http import HTTPStatus

from fastapi import FastAPI, Response
from fastapi.responses import ORJSONResponse, RedirectResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .db import _db_client, dispatch_migration
from .router_rest import router as rest_router
from .router_import import router as import_router
from .settings import settings


# Initialize logging
with open(settings.LOGGING_CONFIG_FILE) as f:
    config = json.loads(f.read())
if settings.LOG_LEVEL:
    config['loggers']['app']['level'] = settings.LOG_LEVEL.upper()
if settings.LOG_FILE:
    config['handlers']['file']['filename'] = settings.LOG_FILE
logging.config.dictConfig(config)
log = logging.getLogger(__name__)


# Instantiate app and its particulars
app = FastAPI(
    title=settings.APP_TITLE,
    description=settings.APP_DESCRIPTION,
    default_response_class=ORJSONResponse,
)

app.include_router(import_router)
app.include_router(rest_router)

app.add_middleware(GZipMiddleware)
app.add_middleware(SessionMiddleware,
                   secret_key=settings.SESSION_COOKIE_SECRET_KEY,
                   max_age=settings.SESSION_COOKIE_MAX_AGE)


@app.on_event('startup')
async def startup_event():
    log.info('App startup')
    dispatch_migration()


@app.on_event("shutdown")
async def shutdown_event():
    _db_client().close()
    log.info('App shutdown')


if settings.DEBUG:
    @app.exception_handler(Exception)
    async def exception_handler(request, exc):
        log.exception('Unhandled error: %s: %s', type(exc).__name__, exc, exc_info=exc)
        return Response(traceback.format_exc(), media_type='text/plain',
                        status_code=HTTPStatus.INTERNAL_SERVER_ERROR)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(str(app.docs_url), status_code=HTTPStatus.SEE_OTHER)
