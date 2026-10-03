"""
The control socket between ``sober-console toggle`` (etc.) and the running UI process.

Plain Unix-socket client with no Qt import, so ``sober-console toggle`` starts instantly --
which matters if you bind it to a KDE shortcut.  The server side lives in ``app.py`` as a
QLocalServer listening on the same absolute path.
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

COMMANDS = ("toggle", "show", "hide", "status", "reload", "quit")


def socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return str(Path(base) / f"sober-console-{os.getuid()}.sock")


def send_command(command: str, timeout: float = 1.0) -> "str | None":
    """Send a command to a running UI process and return its reply; None if there is none."""
    chunks: list = []
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(socket_path())
        s.sendall((command + "\n").encode())
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    except OSError:                       # includes timeouts
        if not chunks:
            return None
    finally:
        s.close()
    return b"".join(chunks).decode(errors="replace").strip()
