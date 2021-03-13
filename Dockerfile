FROM python:3.7-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

ENV UPLOAD_PATH='/uploads'
ENV SESSION_COOKIE_SECRET_KEY="change me secret"

COPY /requirements.txt /
RUN set -eux; \
    pip install -r requirements.txt
COPY /app /app/app
RUN set -eux; \
    mkdir $UPLOAD_PATH; \
    python app/app --help  # smoke test

WORKDIR /app
EXPOSE 8000
VOLUME $UPLOAD_PATH

ENTRYPOINT ["python", "app"]
CMD ["deploy"]
HEALTHCHECK --interval=5m --timeout=3s \
    CMD python -c 'import httpx as r; r.get("http://localhost:8000").raise_for_status()'
