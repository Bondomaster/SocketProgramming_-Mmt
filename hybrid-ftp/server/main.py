"""
server/main.py
==============
Entry point for the Basic Level Hybrid FTP server.

Usage
-----
    python -m server.main [--host HOST] [--port PORT] [--root ROOT]

Defaults: host=0.0.0.0, port=2121, root=./ftp_root

Basic Level: single-threaded — one client at a time (multi-threading is
an Advanced Level requirement). The server accepts a client, handles the
full session, then waits for the next connection.
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
from server.session import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ftp-server")
clients_lock = threading.Lock()
clients: dict[int, dict] = {}


def register_client(session_id: int, info: dict):
    with clients_lock:
        clients[session_id] = info


def unregister_client(session_id: int):
    with clients_lock:
        clients.pop(session_id, None)


def session_worker(ctrl_sock: socket.socket, addr: tuple[str, int], root_dir: Path):
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=root_dir)
    session.cwd = root_dir.resolve()
    
    session_id = id(session)
    register_client(session_id, {"addr": addr, "user": "anonymous"})
    
    log.info("Client connected from %s:%d", *addr)
    ctrl_sock.sendall(format_reply(220, "Hybrid FTP Server Ready (Advanced Level)"))
    
    buf = bytearray()
    try:
        while True:
            line = read_line(ctrl_sock, buf)
            if line is None:
                break  
                
            cmd, args = parse_command(line)
    
            if session.authenticated:
                register_client(session_id, {"addr": addr, "user": session.username})
                
            code, text = dispatch(session, cmd, args)
            ctrl_sock.sendall(format_reply(code, text))
            
            if cmd == "QUIT":
                break
    except Exception as e:
        log.error("Error handling client %s: %s", addr, e)
    finally:
        log.info("Client disconnected: %s:%d", *addr)
        unregister_client(session_id)
        ctrl_sock.close()


def run_server(host: str = "0.0.0.0", port: int = 2121, root_str: str = "./ftp_root"):
    root_dir = Path(root_str).resolve()
    root_dir.mkdir(parents=True, exist_ok=True)

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind((host, port))
    listen_sock.listen(16)
    
    log.info(f"FTP Server running on {host}:{port}, root directory: {root_dir}")

    while True:
        client_sock, client_addr = listen_sock.accept()
        t = threading.Thread(
            target=session_worker, 
            args=(client_sock, client_addr, root_dir),
            daemon=True
        )
        t.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hybrid FTP Server - Advanced Level")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2121)
    parser.add_argument("--root", default="./ftp_root")
    args = parser.parse_args()
    
    run_server(args.host, args.port, args.root)

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
# Session worker — handles one connected client
# ---------------------------------------------------------------------------

def session_worker(ctrl_sock: socket.socket, addr: tuple[str, int], root: Path) -> None:
    """
    Serve a single FTP session.
    Reads commands from *ctrl_sock*, dispatches them, sends replies.
    Runs until the client sends QUIT or drops the connection.
    """
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=root, cwd=root)
    log.info("CONNECT  %s:%d", *addr)

    # Send the welcome banner
    ctrl_sock.sendall(format_reply(220, "Hybrid FTP server ready (Basic Level)"))

    buf = bytearray()
    try:
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

            if cmd == "QUIT":
                log.info("DISCONNECT  %s:%d  (QUIT)", *addr)
                break
    except OSError as exc:
        log.error("SESSION ERROR  %s:%d  %s", *addr, exc)
    finally:
        ctrl_sock.close()


# ---------------------------------------------------------------------------
# Server main loop
# ---------------------------------------------------------------------------

def run_server(host: str = "0.0.0.0", port: int = 2121, root: Path = Path("./ftp_root")) -> None:
    root = root.resolve()
    if not root.exists():
        root.mkdir(parents=True)
        log.info("Created ftp_root at %s", root)

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind((host, port))
    listen_sock.listen(5)
    log.info("FTP server listening on %s:%d  root=%s", host, port, root)
    log.info("Basic Level — single-threaded, serving one client at a time")

    try:
        while True:
            client_sock, client_addr = listen_sock.accept()
            session_worker(client_sock, client_addr, root)
    except KeyboardInterrupt:
        log.info("Server shutting down (KeyboardInterrupt)")
    finally:
        listen_sock.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hybrid FTP Server — Basic Level")
    p.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=2121, help="TCP control port (default: 2121)")
    p.add_argument("--root", default="./ftp_root", help="FTP sandbox root directory")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_server(host=args.host, port=args.port, root=Path(args.root))
