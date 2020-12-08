FROM python:3.7-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

COPY /requirements.txt /
RUN set -eux; \
	pip install -r requirements.txt
COPY /app /app/app
RUN set -eux; \
    mkdir /uploads; \
    chown 10000:10000 /app /uploads; \
    python app/app --help  # smoke test

USER 10000:10000
WORKDIR /app
ENTRYPOINT ["python", "app"]
CMD ["deploy"]
EXPOSE 8000
VOLUME /uploads
ENV UPLOAD_PATH='/uploads'
ENV SESSION_COOKIE_SECRET_KEY="change me secret"
HEALTHCHECK --interval=5m --timeout=3s \
    CMD python -c 'import requests as r; r.get("http://localhost:8000").raise_for_status()'