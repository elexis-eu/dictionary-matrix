import sys
from os.path import dirname

import click
import uvicorn

sys.path.insert(0, dirname(dirname(__file__)))
from app import app, log, settings  # noqa: E402


@click.command(help='Run: `uvicorn --reload=True --log-level=debug app:app`')
def develop():
    uvicorn.run('app:app', reload=True, log_level='debug')


@click.command(help='Run: `gunicorn --config $DEPLOY_CONFIG_FILE app:app`')
def deploy():
    import gunicorn.app.base

    class App(gunicorn.app.base.Application):
        def load_config(self):
            options: dict = {}
            with open(settings.DEPLOYMENT_CONFIG_FILE) as fd:
                exec(fd.read(), None, options)
            for key, value in options.items():
                try:
                    self.cfg.set(key, value)
                except Exception as exc:
                    log.exception(f'Error gunicorn config key: {key!r}. '
                                  f'Exception: {exc}')

        def load(self):
            return app

    App().run()


if __name__ == '__main__':
    main = click.group()(lambda: None)
    main.add_command(develop)
    main.add_command(deploy)
    main()
