"""
server/session.py
=================
Session dataclass — per-client state for the Basic Level FTP server.

Each accepted TCP connection gets its own Session instance that tracks
authentication state, current working directory, and transfer type.
"""

from dataclasses import dataclass, field
from pathlib import Path
import socket
from dataclasses import dataclass, field
from pathlib import Path
import socket

USERS: dict[str, str] = {
    "admin": "1234",
    "alice": "secret",
    "guest": "guest",
}

def check_credentials(username: str, password: str) -> bool:
    """Return True if (username, password) is valid."""
    return USERS.get(username) == password


@dataclass
class Session:
    ctrl_sock: socket.socket       # TCP control socket for this client
    addr: tuple[str, int]          # (ip, port) of the remote client
    root: Path                     # Sandbox root — client cannot escape this
    cwd: Path = field(default_factory=lambda: Path("/"))
    username: str = ""
    authenticated: bool = False
    type_: str = "A"               # 'A' = ASCII (default), 'I' = Binary
    mode: str = "S"                # 'S' = Stream (default), 'B' = Block, 'C' = Compressed

    rename_from: Path | None = None          # Temporary path stored by RNFR, consumed by RNTO
    data_mode: str = "NONE"
    data_peer: tuple[str, int] | None = None
    pasv_sock: socket.socket | None = None

def resolve_path(session: Session, rel: str) -> Path | None:
    if not rel:
        candidate = session.cwd.resolve()
    else:
        rel_path = Path(rel)
        if rel_path.is_absolute():
            candidate = (session.root / rel_path.relative_to(rel_path.anchor)).resolve()
        else:
            candidate = (session.cwd / rel_path).resolve()

    root = session.root.resolve()
    if root != candidate and root not in candidate.parents:
        return None
    return candidate