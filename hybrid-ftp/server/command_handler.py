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
from .session import check_credentials, resolve_path
from common.rdt_sender import send_file
from common.rdt_receiver import recv_file
from common.hashutil import sha256_file
log = logging.getLogger("ftp-server")


DATA_PORT = 2122        # Server's UDP port (STOR: server listens here)
CLIENT_DATA_PORT = 2123 # Client's UDP port (RETR: client listens here)
CHUNK_SIZE = 1024       # bytes per UDP datagram for file transfer

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

def _require_auth(session: "Session") -> tuple[int, str] | None:
    """Return a 530 reply tuple if the session is not authenticated, else None."""
    if not session.authenticated:
        return 530, "Not logged in"
    return None

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


@command("QUIT")
def cmd_quit(session: "Session", args: str) -> tuple[int, str]:
    return 221, "Goodbye"

@command("NOOP")
def cmd_noop(session: "Session", args: str) -> tuple[int, str]:
    return 200, "Command OK"

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
        target = resolve_path(session, args.strip())
        if target is None or not target.exists():
            return 550, "File unavailable" 
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
    target = resolve_path(session, args.strip())
    if target is None or not target.is_file():
        return 550, "File unavailable"
    return 213, str(target.stat().st_size)

@command("MDTM")
def cmd_mdtm(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err
    target = resolve_path(session, args.strip())
    if target is None or not target.is_file():
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
    """Abort the current data transfer.
    Note: Returns 226 as a stub. A true abort would require running the transfer on a separate thread with a cancel flag."""
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
    target = resolve_path(session, filename)
    if target is None or not target.is_file():
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
    target = resolve_path(session, filename)
    if target is None or not target.exists():
        return 550, f"File unavailable: {filename}"
    session.rename_from = target
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
    src = session.rename_from
    dst = resolve_path(session, newname)
    if dst is None:
        return 550, "Invalid destination path"
    try:
        src.rename(dst)
        log.info("RNTO   %s -> %s  user=%s", src.name, newname, session.username)
        session.rename_from = None   # clear after successful rename
        return 250, f"Rename successful: {src.name} -> {newname}"
    except OSError as exc:
        log.error("RNTO   error  %s", exc)
        session.rename_from = None
        return 451, f"Requested action aborted: {exc}"

def _open_server_data_sock() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DATA_PORT))
    sock.settimeout(10.0)   # 10 s receive timeout — avoids hanging forever
    return sock


def _open_client_data_sock(client_ip: str) -> tuple[socket.socket, tuple[str, int]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_addr = (client_ip, CLIENT_DATA_PORT)
    return sock, client_addr

def _get_data_socket_and_peer(session: "Session") -> tuple[socket.socket | None, tuple[str, int] | None, bool]:
    if session.data_mode == "PASSIVE" and session.pasv_sock:
        return session.pasv_sock, None, True
    elif session.data_mode == "ACTIVE" and session.data_peer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return sock, session.data_peer, False
    else:
        return None, None, False


def _send_over_data_channel(session: "Session", data: bytes) -> None:
    sock, peer, is_pasv = _get_data_socket_and_peer(session)
    if not sock:
        return
    try:
        if is_pasv and sock:
            sock.settimeout(5.0)  
            try:
                _, peer = sock.recvfrom(1024)
            except (socket.timeout, OSError):
                log.error("PASV mode: Timeout waiting for client UDP datagram")
                return

        if peer:
            chunks = [data[i:i + CHUNK_SIZE] for i in range(0, max(1, len(data)), CHUNK_SIZE)]
            send_file(sock, peer, chunks)
    except Exception as exc:
        log.error("Error sending data over UDP channel: %s", exc)
    finally:
        if session.pasv_sock:
            session.pasv_sock.close()
            session.pasv_sock = None
            session.data_mode = "NONE"
        elif sock and not is_pasv:
            sock.close()
            session.data_mode = "NONE"


def _recv_over_data_channel(session: "Session", target_path: Path, mode: str = "wb") -> int:
    sock, _, is_pasv = _get_data_socket_and_peer(session)
    
    if not is_pasv and session.data_mode != "PASSIVE" and sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", DATA_PORT))
        except OSError:
            pass
        
    total_bytes = 0
    if sock:
        sock.settimeout(10.0)
        try:
            recv_file(sock, target_path)
            total_bytes = target_path.stat().st_size
        finally:
            if session.pasv_sock:
                session.pasv_sock.close()
                session.pasv_sock = None
                session.data_mode = "NONE"
            else:
                sock.close()
                session.data_mode = "NONE"
    return total_bytes

@command("RETR")
def cmd_retr(session: "Session", args: str) -> tuple[int, str]:
    """
    Download a file: server reads file and sends chunks over UDP to the client.
    EOF is signalled by sending an empty datagram b"".
    """
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None or not target.is_file():
        return 550, f"File unavailable: {args}"

    try:
        total_bytes = 0
        sock, peer, is_pasv = _get_data_socket_and_peer(session)
        if not sock:
            return 425, "Use PORT or PASV first"
        
        session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")
        
        if is_pasv and sock:
            sock.settimeout(5.0)  
            try:
                _, peer = sock.recvfrom(1024)
            except (socket.timeout, OSError):
                log.error("PASV mode: Timeout waiting for client UDP datagram")
                return 425, "Can't open data connection"

        if peer:
            log.info("RETR   %s → %s:%d", target.name, *peer)
            chunks = []
            with open(target, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    chunks.append(chunk)
                    total_bytes += len(chunk)
            send_file(sock, peer, chunks)
            log.info("RETR   complete  file=%s  bytes=%d", target.name, total_bytes)
        return 226, f"Transfer complete ({total_bytes} bytes)"
    except OSError as exc:
        log.error("RETR   error  %s", exc)
        return 451, f"Requested action aborted: {exc}"
    finally:
        if session.pasv_sock:
            session.pasv_sock.close()
            session.pasv_sock = None
            session.data_mode = "NONE"
        elif sock and not is_pasv:
            sock.close()
            session.data_mode = "NONE"


@command("STOR")
def cmd_stor(session: "Session", args: str) -> tuple[int, str]:
    """
    Upload a file: server opens a fixed UDP port and waits for data from client.
    Transfer ends when the client sends an empty datagram.
    """
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None:
        return 550, "Invalid path"
        
    sock, _, _ = _get_data_socket_and_peer(session)
    if not sock:
        return 425, "Use PORT or PASV first"
        
    session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")

    try:
        log.info("STOR   waiting on data channel  file=%s", target.name)
        total_bytes = _recv_over_data_channel(session, target, mode="wb")
        log.info("STOR   complete  file=%s  bytes=%d", target.name, total_bytes)
        return 226, f"Transfer complete ({total_bytes} bytes)"
    except OSError as exc:
        log.error("STOR   error  %s", exc)
        return 451, f"Requested action aborted: {exc}"

@command("CWD")
def cmd_cwd(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    
    session.cwd = target
    return 250, f"Directory changed to {target.name or '/'}"


@command("CDUP")
def cmd_cdup(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    parent = session.cwd.parent
    root = session.root.resolve()
    
    if root not in parent.parents and parent != root:
        parent = root
        
    session.cwd = parent
    return 250, "Directory changed to parent"


@command("MKD")
def cmd_mkd(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None:
        return 550, "Invalid path"
    
    try:
        target.mkdir(parents=False, exist_ok=False)
        return 257, f'"{args.strip()}" directory created'
    except FileExistsError:
        return 550, "Directory already exists"
    except OSError as exc:
        return 450, f"Error creating directory: {exc}"


@command("RMD")
def cmd_rmd(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    
    try:
        target.rmdir()
        return 250, "Directory removed"
    except OSError:
        return 550, "Directory not empty or cannot be removed"


@command("LIST")
def cmd_list(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip()) if args.strip() else session.cwd
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
        
    sock, _, _ = _get_data_socket_and_peer(session)
    if not sock:
        return 425, "Use PORT or PASV first"

    lines = []
    for entry in sorted(target.iterdir()):
        st = entry.stat()
        kind = "d" if entry.is_dir() else "-"
        lines.append(f"{kind} {st.st_size:>10} {entry.name}")
    
    data = "\r\n".join(lines).encode("utf-8")
    session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")
    _send_over_data_channel(session, data)
    return 226, "Directory listing complete"


@command("NLST")
def cmd_nlst(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip()) if args.strip() else session.cwd
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
        
    sock, _, _ = _get_data_socket_and_peer(session)
    if not sock:
        return 425, "Use PORT or PASV first"

    names = "\r\n".join(sorted(e.name for e in target.iterdir()))
    session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")
    _send_over_data_channel(session, names.encode("utf-8"))
    return 226, "Transfer complete"


@command("STOU")
def cmd_stou(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    import uuid
    unique_name = f"{uuid.uuid4().hex[:8]}_{args.strip() or 'file'}"
    target = session.cwd / unique_name
    
    sock, _, _ = _get_data_socket_and_peer(session)
    if not sock:
        return 425, "Use PORT or PASV first"

    session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")
    _recv_over_data_channel(session, target)
    return 226, f"Transfer complete, stored as {unique_name}"


@command("APPE")
def cmd_appe(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    target = resolve_path(session, args.strip())
    if target is None:
        return 550, "Invalid path"
        
    sock, _, _ = _get_data_socket_and_peer(session)
    if not sock:
        return 425, "Use PORT or PASV first"

    session.ctrl_sock.sendall(b"150 Opening UDP Data Channel\r\n")
    _recv_over_data_channel(session, target, mode="ab")
    return 226, "Transfer complete"

@command("PASV")
def cmd_pasv(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    if session.pasv_sock:
        session.pasv_sock.close()
        session.pasv_sock = None
        
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.bind(("0.0.0.0", 0))
    data_sock.settimeout(10.0)
    
    _, port = data_sock.getsockname()
    session.pasv_sock = data_sock
    session.data_mode = "PASSIVE"

    server_ip = session.ctrl_sock.getsockname()[0]
    if server_ip == "0.0.0.0":
        server_ip = "127.0.0.1"

    ip_parts = server_ip.split(".")
    p1, p2 = port >> 8, port & 0xFF
    
    pasv_str = f"{','.join(ip_parts)},{p1},{p2}"
    return 227, f"Entering Passive Mode ({pasv_str})"


@command("PORT")
def cmd_port(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err

    if session.pasv_sock:
        session.pasv_sock.close()
        session.pasv_sock = None

    try:
        parts = list(map(int, args.strip().split(",")))
        ip = ".".join(map(str, parts[:4]))
        port = (parts[4] << 8) + parts[5]
        
        session.data_peer = (ip, port)
        session.data_mode = "ACTIVE"
        return 200, "PORT command successful"
    except Exception:
        return 501, "Syntax error in parameters"

@command("HASH")
def cmd_hash(session: "Session", args: str) -> tuple[int, str]:
    err = _require_auth(session)
    if err:
        return err
    filename = args.strip()
    if not filename:
        return 501, "Syntax error: filename required"
    target = resolve_path(session, filename)
    if target is None or not target.is_file():
        return 550, f"File unavailable: {filename}"
    digest = sha256_file(target)
    return 213, f"SHA-256 {digest}"