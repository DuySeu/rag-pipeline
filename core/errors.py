from __future__ import annotations

from enum import IntEnum


# Exit codes shared by the CLIs; 1 and 2 stay reserved for crashes and argparse
class ErrorKind(IntEnum):
    CONFIG = 5
    RATE_LIMIT = 6
    QUOTA = 7
    API = 8


# An expected failure that a CLI prints as `[ERROR <kind>] <message>` before exiting
class CliError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
