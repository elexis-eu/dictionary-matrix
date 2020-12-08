import re
from enum import Enum, auto
from typing import NoReturn, Type


class _AutoEnum(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name

    @staticmethod
    def _auto_range(n):
        return [auto() for _ in range(n)]


def enum_values(enum_type: Type[Enum]):
    return [i.value for i in enum_type]


def safe_path(part):
    return re.sub(r'[^\w.-]', '_', part)


def assert_never(value: NoReturn) -> NoReturn:
    # https://hakibenita.com/python-mypy-exhaustive-checking
    assert False, f'Unhandled value: {value} ({type(value).__name__})'
