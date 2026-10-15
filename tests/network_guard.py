import os
import sys


CREDENTIALS_PATH = "/home/ubuntu/projects/need-radar/credentials.env"
DEEPSEEK_CREDENTIALS_PATH = "/home/ubuntu/.config/need-radar/deepseek.env"
DISCORD_CREDENTIALS_PATH = "/home/ubuntu/.config/need-radar/discord.env"
LIVE_STATE_PATH = "/home/ubuntu/.local/state/need-radar"


def _paths(event, arguments):
    if event in {"open", "os.stat", "os.lstat", "os.mkdir", "os.rmdir", "os.remove", "os.unlink", "os.chmod", "os.chown", "os.utime", "sqlite3.connect"}:
        return arguments[:1]
    if event in {"os.rename", "os.replace"}:
        return arguments[:2]
    return ()


def _deny(event, arguments):
    if event.startswith("socket."):
        raise PermissionError("network access is disabled in tests")
    denied_path = os.environ.get("NEED_RADAR_TEST_DENY_PATH")
    for candidate in _paths(event, arguments):
        try:
            path = os.path.abspath(os.fsdecode(candidate))
        except TypeError:
            continue
        if denied_path and path == os.path.abspath(denied_path):
            raise PermissionError("credential access is disabled in tests")
        if path in {CREDENTIALS_PATH, DEEPSEEK_CREDENTIALS_PATH, DISCORD_CREDENTIALS_PATH}:
            raise PermissionError("credential access is disabled in tests")
        if path == LIVE_STATE_PATH or path.startswith(LIVE_STATE_PATH + os.sep):
            raise PermissionError("live state access is disabled in tests")


def install():
    sys.addaudithook(_deny)
