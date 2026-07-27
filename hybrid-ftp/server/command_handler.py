"""
server/command_handler.py
==========================
FTP command dispatch table — Basic Level.

Implements all commands required by the Basic Level spec:
  USER, PASS, QUIT, NOOP, PWD, TYPE, STAT, SIZE, MDTM, HELP, RETR, STOR

Each handler has the signature:
    (session: Session, args: str) -> tuple[int, str]
and returns an FTP reply code + message.

UDP Data Channel (Basic Level — fixed ports, no Active/Passive switching):
  - Server listens on DATA_PORT (2122) for incoming STOR data
  - Server sends RETR data to client at CLIENT_DATA_PORT (2123)
  - EOF is signalled with an empty datagram b""
"""

from __future__ import annotations

import datetime
import logging
import socket
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import Session

from .session import check_credentials

log = logging.getLogger("ftp-server")

# ---------------------------------------------------------------------------
# UDP data channel ports (Basic Level: fixed, no negotiation)
# ---------------------------------------------------------------------------

DATA_PORT = 2122        # Server's UDP port (STOR: server listens here)
CLIENT_DATA_PORT = 2123 # Client's UDP port (RETR: client listens here)
CHUNK_SIZE = 1024       # bytes per UDP datagram for file transfer

# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

Handler = Callable[["Session", str], tuple[int, str]]
COMMANDS: dict[str, Handler] = {}


def command(name: str):
    """Decorator: register a function as the handler for FTP command *name*."""
    def deco(fn: Handler) -> Handler:
        COMMANDS[name] = fn
        return fn
    return deco


def dispatch(session: "Session", cmd: str, args: str) -> tuple[int, str]:
    """Look up and call the handler for *cmd*, or return 502 if unknown."""
    handler = COMMANDS.get(cmd)
    if handler is None:
        return 502, "Command not implemented"
    return handler(session, args)


# ---------------------------------------------------------------------------
# Auth guard helper
# ---------------------------------------------------------------------------

def _require_auth(session: "Session") -> tuple[int, str] | None:
    """Return a 530 reply tuple if the session is not authenticated, else None."""
    if not session.authenticated:
        return 530, "Not logged in"
    return None


# ---------------------------------------------------------------------------
# Authentication commands
# ---------------------------------------------------------------------------

@command("USER")
def cmd_user(session: "Session", args: str) -> tuple[int, str]:
    session.username = args.strip()
    if not session.username:
        return 501, "Syntax error: username required"
    return 331, f"Username '{session.username}' OK, need password"


@command("PASS")
def cmd_pass(session: "Session", args: str) -> tuple[int, str]:
    if not session.username:
        return 503, "Bad sequence of commands: send USER first"
    session.authenticated = check_credentials(session.username, args)
    if session.authenticated:
        log.info("LOGIN  user=%s addr=%s", session.username, session.addr)
        return 230, "Login successful"
    log.warning("FAILED LOGIN  user=%s addr=%s", session.username, session.addr)
    return 530, "Not logged in — wrong password"


# ---------------------------------------------------------------------------
# Session control commands
# ---------------------------------------------------------------------------

@command("QUIT")
def cmd_quit(session: "Session", args: str) -> tuple[int, str]:
    return 221, "Goodbye"


@command("NOOP")
def cmd_noop(session: "Session", args: str) -> tuple[int, str]:
    return 200, "Command OK"


# ---------------------------------------------------------------------------
# Info/status commands
# ---------------------------------------------------------------------------

@command("PWD")
def cmd_pwd(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err
    return 257, f'"{session.cwd}" is the current directory'


@command("TYPE")
def cmd_type(session: "Session", args: str) -> tuple[int, str]:
    t = args.strip().upper()
    if t not in ("A", "I"):
        return 504, "Command not implemented for that parameter"
    session.type_ = t
    label = "ASCII" if t == "A" else "Binary/Image"
    return 200, f"Type set to {t} ({label})"


@command("STAT")
def cmd_stat(session: "Session", args: str) -> tuple[int, str]:
    if args:
        target = session.cwd / args.strip()
        if not target.exists():
            return 450, "File unavailable"
        return 213, f"{target.name} {target.stat().st_size} bytes"
    return 211, (
        f"FTP server status — connected as {session.username or 'anonymous'}, "
        f"cwd={session.cwd}, type={session.type_}"
    )


@command("SIZE")
def cmd_size(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err
    target = session.cwd / args.strip()
    if not target.is_file():
        return 550, "File unavailable"
    return 213, str(target.stat().st_size)


@command("MDTM")
def cmd_mdtm(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err
    target = session.cwd / args.strip()
    if not target.is_file():
        return 550, "File unavailable"
    ts = datetime.datetime.fromtimestamp(target.stat().st_mtime)
    return 213, ts.strftime("%Y%m%d%H%M%S")


@command("HELP")
def cmd_help(session: "Session", args: str) -> tuple[int, str]:
    if args and args.strip().upper() in COMMANDS:
        return 214, f"Syntax for {args.strip().upper()}: see project spec"
    return 214, "Commands: " + " ".join(sorted(COMMANDS.keys()))


@command("SYST")
def cmd_syst(session: "Session", args: str) -> tuple[int, str]:
    """Identify the server operating system (RFC 959)."""
    return 215, "UNIX Type: L8"


@command("ABOR")
def cmd_abor(session: "Session", args: str) -> tuple[int, str]:
    """Abort the current data transfer (Basic Level: simple acknowledgement stub)."""
    return 226, "ABOR command successful"


@command("MODE")
def cmd_mode(session: "Session", args: str) -> tuple[int, str]:
    """Set transfer mode: S (Stream), B (Block), C (Compressed)."""
    m = args.strip().upper()
    if m not in ("S", "B", "C"):
        return 504, "Command not implemented for that parameter"
    session.mode = m
    labels = {"S": "Stream", "B": "Block", "C": "Compressed"}
    return 200, f"Mode set to {m} ({labels[m]})"


@command("DELE")
def cmd_dele(session: "Session", args: str) -> tuple[int, str]:
    """Delete a file from the current working directory."""
    err = _require_auth(session)
    if err:
        return err
    filename = args.strip()
    if not filename:
        return 501, "Syntax error: filename required"
    target = session.cwd / filename
    if not target.is_file():
        return 550, f"File unavailable: {filename}"
    try:
        target.unlink()
        log.info("DELE   file=%s  user=%s", filename, session.username)
        return 250, f"File deleted: {filename}"
    except OSError as exc:
        log.error("DELE   error  %s", exc)
        return 451, f"Requested action aborted: {exc}"


@command("RNFR")
def cmd_rnfr(session: "Session", args: str) -> tuple[int, str]:
    """Rename From — specify the source path for a rename operation."""
    err = _require_auth(session)
    if err:
        return err
    filename = args.strip()
    if not filename:
        return 501, "Syntax error: filename required"
    target = session.cwd / filename
    if not target.exists():
        return 550, f"File unavailable: {filename}"
    session.rename_from = str(target)
    return 350, f"Ready for RNTO (rename \'{filename}\')"


@command("RNTO")
def cmd_rnto(session: "Session", args: str) -> tuple[int, str]:
    """Rename To — execute the rename using the path stored by RNFR."""
    err = _require_auth(session)
    if err:
        return err
    if not session.rename_from:
        return 503, "Bad sequence of commands: send RNFR first"
    newname = args.strip()
    if not newname:
        return 501, "Syntax error: new filename required"
    src = Path(session.rename_from)
    dst = session.cwd / newname
    try:
        src.rename(dst)
        log.info("RNTO   %s -> %s  user=%s", src.name, newname, session.username)
        session.rename_from = ""   # clear after successful rename
        return 250, f"Rename successful: {src.name} -> {newname}"
    except OSError as exc:
        log.error("RNTO   error  %s", exc)
        session.rename_from = ""
        return 451, f"Requested action aborted: {exc}"


# ---------------------------------------------------------------------------
# UDP Data Channel helpers (Basic Level — fixed ports, no reliability)
# ---------------------------------------------------------------------------

def _open_server_data_sock() -> socket.socket:
    """
    Open a UDP socket on the fixed server data port (DATA_PORT).
    Used by STOR to receive incoming file data from the client.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DATA_PORT))
    sock.settimeout(10.0)   # 10 s receive timeout — avoids hanging forever
    return sock


def _open_client_data_sock(client_ip: str) -> tuple[socket.socket, tuple[str, int]]:
    """
    Open a UDP socket for sending to the client's data port (CLIENT_DATA_PORT).
    Returns (sock, client_data_addr).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_addr = (client_ip, CLIENT_DATA_PORT)
    return sock, client_addr


# ---------------------------------------------------------------------------
# File transfer commands
# ---------------------------------------------------------------------------

@command("RETR")
def cmd_retr(session: "Session", args: str) -> tuple[int, str]:
    """
    Download a file: server reads file and sends chunks over UDP to the client.
    EOF is signalled by sending an empty datagram b"".
    """
    err = _require_auth(session)
    if err:
        return err

    target = session.cwd / args.strip()
    if not target.is_file():
        return 550, f"File unavailable: {args}"

    client_ip = session.addr[0]
    data_sock, client_data_addr = _open_client_data_sock(client_ip)

    try:
        total_bytes = 0
        log.info("RETR   %s → %s:%d", target.name, *client_data_addr)
        with open(target, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                data_sock.sendto(chunk, client_data_addr)
                total_bytes += len(chunk)
        # Signal EOF with an empty datagram
        data_sock.sendto(b"", client_data_addr)
        log.info("RETR   complete  file=%s  bytes=%d", target.name, total_bytes)
        return 226, f"Transfer complete ({total_bytes} bytes)"
    except OSError as exc:
        log.error("RETR   error  %s", exc)
        return 451, f"Requested action aborted: {exc}"
    finally:
        data_sock.close()


@command("STOR")
def cmd_stor(session: "Session", args: str) -> tuple[int, str]:
    """
    Upload a file: server opens a fixed UDP port and waits for data from client.
    Transfer ends when the client sends an empty datagram.
    """
    err = _require_auth(session)
    if err:
        return err

    target = session.cwd / args.strip()
    data_sock = _open_server_data_sock()

    try:
        total_bytes = 0
        log.info("STOR   waiting on UDP port %d  file=%s", DATA_PORT, target.name)
        with open(target, "wb") as f:
            while True:
                try:
                    chunk, _ = data_sock.recvfrom(CHUNK_SIZE + 64)
                except socket.timeout:
                    log.warning("STOR   timed out waiting for data  file=%s", target.name)
                    return 426, "Connection closed; transfer aborted (timeout)"
                if not chunk:   # empty datagram = EOF signal
                    break
                f.write(chunk)
                total_bytes += len(chunk)
        log.info("STOR   complete  file=%s  bytes=%d", target.name, total_bytes)
        return 226, f"Transfer complete ({total_bytes} bytes)"
    except OSError as exc:
        log.error("STOR   error  %s", exc)
        return 451, f"Requested action aborted: {exc}"
    finally:
        data_sock.close()
