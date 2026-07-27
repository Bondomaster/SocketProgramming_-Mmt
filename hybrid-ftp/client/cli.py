"""
client/cli.py
=============
Interactive CLI for the Basic Level Hybrid FTP client.

Handles the command-read-print loop and the special logic for file transfer
commands (STOR/RETR) that require a parallel UDP data channel.

UDP Data Channel (Basic Level — fixed ports, mirrors server):
  - STOR: client opens UDP port CLIENT_DATA_PORT (2123), sends chunks to server DATA_PORT (2122)
  - RETR: client opens UDP port CLIENT_DATA_PORT (2123), receives chunks from server

EOF is signalled by an empty datagram b"".
"""

from __future__ import annotations

import socket
from pathlib import Path

from common.protocol import format_reply, recv_reply

# ---------------------------------------------------------------------------
# Fixed UDP port config (must match server/command_handler.py)
# ---------------------------------------------------------------------------

SERVER_DATA_PORT = 2122    # Server listens here for STOR uploads
CLIENT_DATA_PORT = 2123    # Client listens here for RETR downloads
CHUNK_SIZE = 1024


# ---------------------------------------------------------------------------
# UDP helpers
# ---------------------------------------------------------------------------

def _send_file_udp(server_ip: str, local_path: Path) -> int:
    """
    Upload *local_path* to the server via UDP (STOR helper).
    Chunks the file into CHUNK_SIZE datagrams, ends with empty datagram.
    Returns total bytes sent.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (server_ip, SERVER_DATA_PORT)
    total = 0
    try:
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendto(chunk, dest)
                total += len(chunk)
        sock.sendto(b"", dest)   # EOF signal
    finally:
        sock.close()
    return total


def _recv_file_udp(out_path: Path, timeout: float = 10.0) -> int:
    """
    Download a file from the server via UDP (RETR helper).
    Binds CLIENT_DATA_PORT, receives chunks until empty datagram.
    Returns total bytes received.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CLIENT_DATA_PORT))
    sock.settimeout(timeout)
    total = 0
    try:
        with open(out_path, "wb") as f:
            while True:
                try:
                    chunk, _ = sock.recvfrom(CHUNK_SIZE + 64)
                except socket.timeout:
                    print("[ERROR] Timeout waiting for data from server")
                    break
                if not chunk:   # empty datagram = EOF
                    break
                f.write(chunk)
                total += len(chunk)
    finally:
        sock.close()
    return total


# ---------------------------------------------------------------------------
# Main client loop
# ---------------------------------------------------------------------------

def run_client(host: str, port: int) -> None:
    ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ctrl_sock.connect((host, port))
    except ConnectionRefusedError:
        print(f"[ERROR] Cannot connect to {host}:{port} — is the server running?")
        return

    print(f"[STATUS] Connected to {host}:{port}")
    # Print server welcome banner
    banner = recv_reply(ctrl_sock)
    print(f"<<  {banner}")

    while True:
        try:
            raw = input("ftp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[STATUS] Disconnected")
            break

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].upper()
        args = parts[1] if len(parts) > 1 else ""

        # ----------------------------------------------------------------
        # STOR — special handling: send file over UDP, then wait for reply
        # ----------------------------------------------------------------
        if cmd == "STOR":
            local_path = Path(args)
            if not local_path.is_file():
                print(f"[ERROR] Local file not found: {local_path}")
                continue
            # Tell server we want to upload
            ctrl_sock.sendall(f"STOR {local_path.name}\r\n".encode())
            print(f"[STATUS] Uploading '{local_path.name}' ...")
            bytes_sent = _send_file_udp(host, local_path)
            # Now read the server's reply
            reply = recv_reply(ctrl_sock)
            print(f"<<  {reply}")
            print(f"[STATUS] Upload complete — {bytes_sent} bytes sent")
            continue

        # ----------------------------------------------------------------
        # RETR — special handling: receive file over UDP, then print reply
        # ----------------------------------------------------------------
        if cmd == "RETR":
            ctrl_sock.sendall(f"RETR {args}\r\n".encode())
            out_path = Path(args).name   # save to current directory
            print(f"[STATUS] Downloading '{args}' → '{out_path}' ...")
            bytes_recv = _recv_file_udp(Path(out_path))
            reply = recv_reply(ctrl_sock)
            print(f"<<  {reply}")
            if bytes_recv > 0:
                print(f"[STATUS] Download complete — {bytes_recv} bytes received → {out_path}")
            else:
                print("[ERROR] No data received (check server logs)")
            continue

        # ----------------------------------------------------------------
        # All other commands — send and print reply
        # ----------------------------------------------------------------
        ctrl_sock.sendall((raw + "\r\n").encode())
        reply = recv_reply(ctrl_sock)
        print(f"<<  {reply}")

        if cmd == "QUIT":
            print("[STATUS] Connection closed")
            break

    ctrl_sock.close()
