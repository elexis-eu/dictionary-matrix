import logging
from dataclasses import dataclass
from multiprocessing import Process
from queue import SimpleQueue
from threading import Thread
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class Task:
    target: Callable
    queue: SimpleQueue
    n_workers: int
    name: str = 'task'
    timeout: Optional[float] = None

    def start(self):
        log.info(f'Init {self.n_workers} {self.name} worker threads')
        for i in range(self.n_workers):
            Thread(
                target=self._worker,
                args=(i, self.queue),
                name=f'{self.name}-{i}',
                daemon=True,  # join thread on process exit
            ).start()

    def _worker(self, thread_id, queue):
        for arg in iter(queue.get, None):  # type: str
            # Run worker target in yet a subprocess.
            # Malicious input may crash the process (e.g. lxml is C),
            # and we don't want that to affect the app, do we?
            proc = Process(target=self.target,
                           args=(arg,),
                           name=f'{self.name}-{thread_id}-{arg}',
                           daemon=True)
            proc.start()
            proc.join(timeout=self.timeout)
            if proc.exitcode != 0:
                log.error(f'Task {self.name} failed for {arg}')
