"""
client/cli.py
=============
Interactive CLI for the Advanced Level Hybrid FTP client.

Handles the command-read-print loop and the special logic for file transfer
commands (STOR/RETR/LIST/NLST) that require a parallel UDP data channel.

UDP Data Channel:
  - Uses PASV mode to get an ephemeral UDP port from the server to ensure concurrency.
  - Checks expected sizes for RETR/STOR to catch silent UDP corruption/loss.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

from common.protocol import format_reply, recv_reply

CHUNK_SIZE = 1024

# ---------------------------------------------------------------------------
# UDP helpers
# ---------------------------------------------------------------------------

def _send_file_udp(dest_addr: tuple[str, int], local_path: Path) -> int:
    """
    Upload *local_path* to the server via UDP (STOR helper).
    Chunks the file into CHUNK_SIZE datagrams, ends with empty datagram.
    Returns total bytes sent.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    total = 0
    try:
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendto(chunk, dest_addr)
                total += len(chunk)
        sock.sendto(b"", dest_addr)   # EOF signal
    finally:
        sock.close()
    return total


def _recv_file_udp(server_addr: tuple[str, int], out_path: Path, timeout: float = 10.0) -> int:
    """
    Download a file from the server via UDP (RETR helper).
    Sends dummy datagram to punch hole for PASV, receives chunks until empty datagram.
    Returns total bytes received.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    total = 0
    try:
        sock.sendto(b"HELLO", server_addr) # punch hole / inform server of port
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

def _recv_text_udp(server_addr: tuple[str, int], timeout: float = 5.0) -> str:
    """
    Receive text data (for LIST / NLST) from Server via UDP.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    chunks = []
    try:
        sock.sendto(b"HELLO", server_addr) # punch hole
        while True:
            try:
                chunk, _ = sock.recvfrom(CHUNK_SIZE + 64)
            except socket.timeout:
                break
            if not chunk:   # Empty datagram = EOF
                break
            chunks.append(chunk)
    finally:
        sock.close()
    
    return b"".join(chunks).decode("utf-8", errors="replace")


def parse_pasv_reply(reply: str) -> tuple[str, int] | None:
    match = re.search(r'\((\d+,\d+,\d+,\d+,\d+,\d+)\)', reply)
    if not match:
        return None
    parts = list(map(int, match.group(1).split(',')))
    ip = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}"
    port = (parts[4] << 8) + parts[5]
    return ip, port

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

        if cmd in ("STOR", "RETR", "LIST", "NLST"):
            # 1. Issue PASV
            ctrl_sock.sendall(b"PASV\r\n")
            pasv_reply = recv_reply(ctrl_sock)
            print(f"<<  {pasv_reply}")
            pasv_addr = parse_pasv_reply(pasv_reply)
            if not pasv_addr:
                print("[ERROR] PASV failed, aborting command")
                continue

            if cmd == "STOR":
                local_path = Path(args)
                if not local_path.is_file():
                    print(f"[ERROR] Local file not found: {local_path}")
                    continue
                file_size = local_path.stat().st_size
                ctrl_sock.sendall(f"STOR {local_path.name}\r\n".encode())
                initial_reply = recv_reply(ctrl_sock)
                print(f"<<  {initial_reply}")

                if initial_reply.startswith("150"):
                    print(f"[STATUS] Uploading '{local_path.name}' ({file_size} bytes) ...")
                    bytes_sent = _send_file_udp(pasv_addr, local_path)
                    final_reply = recv_reply(ctrl_sock)
                    print(f"<<  {final_reply}")
                    
                    match = re.search(r'\((\d+)\s+bytes\)', final_reply)
                    if match:
                        server_bytes = int(match.group(1))
                        if server_bytes == file_size:
                            print(f"[STATUS] Upload complete and verified ({file_size} bytes).")
                        else:
                            print(f"[ERROR] UDP data loss! Sent {file_size} bytes, but server received {server_bytes}.")
                    else:
                        print(f"[STATUS] Upload complete — {bytes_sent} bytes sent")
                else:
                    print("[ERROR] Upload aborted.")
            
            elif cmd == "RETR":
                expected_size = -1
                ctrl_sock.sendall(f"SIZE {args}\r\n".encode())
                size_reply = recv_reply(ctrl_sock)
                if size_reply.startswith("213"):
                    expected_size = int(size_reply.split()[1])

                ctrl_sock.sendall(f"RETR {args}\r\n".encode())
                initial_reply = recv_reply(ctrl_sock)
                print(f"<<  {initial_reply}")
                if initial_reply.startswith("150"):
                    out_path = Path(args).name
                    print(f"[STATUS] Downloading '{args}' → '{out_path}' ...")
                    bytes_recv = _recv_file_udp(pasv_addr, Path(out_path))
                    final_reply = recv_reply(ctrl_sock)
                    print(f"<<  {final_reply}")
                    
                    if expected_size != -1:
                        if bytes_recv == expected_size:
                            print(f"[STATUS] Download complete and verified ({bytes_recv} bytes) → {out_path}")
                        else:
                            print(f"[ERROR] UDP data loss! Expected {expected_size} bytes, but received {bytes_recv}.")
                    elif bytes_recv > 0:
                        print(f"[STATUS] Download complete — {bytes_recv} bytes received → {out_path}")
                    else:
                        print("[ERROR] No data received (check server logs)")
                else:
                    print("[ERROR] Download aborted.")
            
            elif cmd in ("LIST", "NLST"):
                ctrl_sock.sendall((raw + "\r\n").encode())
                initial_reply = recv_reply(ctrl_sock)
                print(f"<<  {initial_reply}")
                
                if initial_reply.startswith("150") or initial_reply.startswith("125"):
                    text = _recv_text_udp(pasv_addr)
                    if text:
                        print(text, end="")
                        if not text.endswith('\n'):
                            print()
                    final_reply = recv_reply(ctrl_sock)
                    print(f"<<  {final_reply}")
            
            continue

        # All other commands
        ctrl_sock.sendall((raw + "\r\n").encode())
        reply = recv_reply(ctrl_sock)
        print(f"<<  {reply}")

        if cmd == "QUIT":
            print("[STATUS] Connection closed")
            break

    ctrl_sock.close()