ELEXIS Dictionary Matrix
========================

A lexicographic web API built with [Python], [FastAPI], [MongoDB].

[Python]: https://www.python.org
[FastAPI]: https://fastapi.tiangolo.com
[MongoDB]: https://www.mongodb.com

### Install

    $ pip install -r requirements-dev.txt

### Run

    $ python app develop  # For development
    $ python app deploy   # For production

### Test

    $ pytest
    $ flake8
    $ mypy app tests

### Configure

Configuration via environment variables (`.env` file supported).
For a list of configuration options, see `app.settings` module.
