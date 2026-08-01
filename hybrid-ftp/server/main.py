"""
server/main.py
==============
Entry point for the Hybrid FTP server.

Usage
-----
    python -m server.main [--host HOST] [--port PORT] [--root ROOT]

Defaults: host=0.0.0.0, port=2121, root=./ftp_root

Threaded server: each accepted client is handled on its own thread, so
multiple clients can be served concurrently (Advanced Level requirement).
This also works perfectly fine for the Basic Level case of a single
client — a thread pool of size 1 in practice behaves the same as a
sequential single-client loop, just with headroom for more.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
from pathlib import Path

from common.protocol import format_reply, parse_command, read_line
from server.session import Session
from server.command_handler import dispatch

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("ftp-server")

# ---------------------------------------------------------------------------
# Connected-client registry (shared across threads — protected by a lock)
# ---------------------------------------------------------------------------

clients_lock = threading.Lock()
clients: dict[int, dict] = {}


def register_client(session_id: int, info: dict) -> None:
    with clients_lock:
        clients[session_id] = info


def unregister_client(session_id: int) -> None:
    with clients_lock:
        clients.pop(session_id, None)


# ---------------------------------------------------------------------------
# Session worker — handles one connected client (runs inside its own thread)
# ---------------------------------------------------------------------------

def session_worker(ctrl_sock: socket.socket, addr: tuple[str, int], root: Path) -> None:
    """
    Serve a single FTP session.
    Reads commands from *ctrl_sock*, dispatches them, sends replies.
    Runs until the client sends QUIT or drops the connection.
    """
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=root, cwd=root)
    session_id = id(session)
    buf = bytearray()

    try:
        register_client(session_id, {"addr": addr, "user": "anonymous"})
        log.info("CONNECT  %s:%d", *addr)
        ctrl_sock.sendall(format_reply(220, "Hybrid FTP server ready"))

        while True:
            line = read_line(ctrl_sock, buf)
            if line is None:
                log.info("DISCONNECT  %s:%d  (connection dropped)", *addr)
                break

            cmd, args = parse_command(line)
            log.info("CMD    %s %s  (user=%s)", cmd, args, session.username or "<none>")

            code, text = dispatch(session, cmd, args)
            ctrl_sock.sendall(format_reply(code, text))
            log.info("REPLY  %d %s", code, text)

            # Keep the client registry up to date with the real username
            # once login succeeds.
            if session.authenticated:
                register_client(session_id, {"addr": addr, "user": session.username})

            if cmd == "QUIT":
                log.info("DISCONNECT  %s:%d  (QUIT)", *addr)
                break
    except Exception as exc:
        log.error("SESSION ERROR  %s:%d  %s", *addr, exc)
    finally:
        unregister_client(session_id)
        if session.pasv_sock is not None:
            try:
                session.pasv_sock.close()
            except OSError:
                pass
        try:
            ctrl_sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Server main loop
# ---------------------------------------------------------------------------

def run_server(host: str = "0.0.0.0", port: int = 2121, root: Path = Path("./ftp_root")) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind((host, port))
    listen_sock.listen(16)
    log.info("FTP server listening on %s:%d  root=%s", host, port, root)

    try:
        while True:
            client_sock, client_addr = listen_sock.accept()
            t = threading.Thread(
                target=session_worker,
                args=(client_sock, client_addr, root),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        log.info("Server shutting down (KeyboardInterrupt)")
    finally:
        listen_sock.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hybrid FTP Server")
    p.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=2121, help="TCP control port (default: 2121)")
    p.add_argument("--root", default="./ftp_root", help="FTP sandbox root directory")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_server(host=args.host, port=args.port, root=Path(args.root))