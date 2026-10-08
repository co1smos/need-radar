import os
import sys


CREDENTIALS_PATH = "/home/ubuntu/projects/need-radar/credentials.env"
LIVE_STATE_PATH = "/home/ubuntu/.local/state/need-radar"


def _deny(event, arguments):
    if event.startswith("socket."):
        raise PermissionError("network access is disabled in tests")
    if event == "open" and arguments:
        try:
            path = os.path.abspath(os.fspath(arguments[0]))
        except TypeError:
            return
        if path == CREDENTIALS_PATH:
            raise PermissionError("credential access is disabled in tests")
        if path == LIVE_STATE_PATH or path.startswith(LIVE_STATE_PATH + os.sep):
            raise PermissionError("live state access is disabled in tests")


def install():
    sys.addaudithook(_deny)
