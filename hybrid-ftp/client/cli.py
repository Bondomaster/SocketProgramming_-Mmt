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
from common.protocol import recv_reply
from common.rdt_sender import send_file, make_fault_injector
from common.rdt_receiver import recv_file
from common.hashutil import sha256_file
from rich.console import Console

console = Console()
CHUNK_SIZE = 1024
SERVER_DATA_PORT = 2122

# UDP helpers
def _send_file_udp(dest_addr: tuple[str, int], local_path: Path, simulate_faults=None) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    total = 0
    try:
        chunks = []
        with open(local_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                chunks.append(chunk)
                total += len(chunk)
        send_file(sock, dest_addr, chunks, simulate_faults=simulate_faults)
    finally:
        sock.close()
    return total

def _recv_file_udp(server_addr: tuple[str, int], out_path: Path, timeout: float = 10.0) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    total = 0
    try:
        sock.sendto(b"HELLO", server_addr)
        recv_file(sock, out_path)
        total = out_path.stat().st_size
    finally:
        sock.close()
    return total

def _recv_text_udp(server_addr: tuple[str, int], timeout: float = 5.0) -> str:
    """
    Receive text data (for LIST / NLST) from Server via UDP using RDT.
    """
    import tempfile
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"HELLO", server_addr) # punch hole
        tmp_path = Path(tempfile.mktemp())
        recv_file(sock, tmp_path)
        if tmp_path.exists():
            text = tmp_path.read_text(encoding="utf-8", errors="replace")
            tmp_path.unlink()
            return text
        return ""
    finally:
        sock.close()

def _recv_file_on_socket(sock: socket.socket, out_path: Path, timeout: float = 10.0) -> int:
    sock.settimeout(timeout)
    total = 0
    try:
        recv_file(sock, out_path)
        if out_path.exists():
            total = out_path.stat().st_size
    except Exception as e:
        console.print(f"[ERROR] Active mode download error: {e}")
    return total

def _recv_text_on_socket(sock: socket.socket, timeout: float = 5.0) -> str:
    """Phiên bản Active Mode của _recv_text_udp — không cần punch-hole."""
    import tempfile
    sock.settimeout(timeout)
    try:
        tmp_path = Path(tempfile.mktemp())
        recv_file(sock, tmp_path)
        if tmp_path.exists():
            text = tmp_path.read_text(encoding="utf-8", errors="replace")
            tmp_path.unlink()
            return text
        return ""
    except Exception as e:
        console.print(f"[ERROR] _recv_text_on_socket error: {e}")
        return ""

def parse_pasv_reply(reply: str) -> tuple[str, int] | None:
    match = re.search(r'\((\d+,\d+,\d+,\d+,\d+,\d+)\)', reply)
    if not match:
        return None
    parts = list(map(int, match.group(1).split(',')))
    ip = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}"
    port = (parts[4] << 8) + parts[5]
    return ip, port

def run_client(host: str, port: int, drop_rate: float = 0.0, corrupt_rate: float = 0.0) -> None:
    ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ctrl_sock.connect((host, port))
    except ConnectionRefusedError:
        console.print(f"[ERROR] Cannot connect to {host}:{port} — is the server running?")
        return

    console.print(f"[STATUS] Connected to {host}:{port}")
    # console.print server welcome banner
    banner = recv_reply(ctrl_sock)
    console.print(f"<<  {banner}")
    transfer_mode = "PASV"
    active_sock: socket.socket | None = None
    
    fault_injector = None
    if drop_rate > 0 or corrupt_rate > 0:
        fault_injector = make_fault_injector(drop_rate, corrupt_rate)
        console.print(f"[STATUS] Fault Injector ACTIVE (Drop: {drop_rate}, Corrupt: {corrupt_rate})")

    while True:
        try:
            raw = input("ftp> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[STATUS] Disconnected")
            break

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].upper()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "PORT" and not args:
            if active_sock is not None:
                active_sock.close()
            active_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            active_sock.bind(("0.0.0.0", 0))
            client_port = active_sock.getsockname()[1]
            client_ip = ctrl_sock.getsockname()[0]
            ip_parts = client_ip.split(".")
            p1, p2 = client_port >> 8, client_port & 0xFF
            port_cmd = f"PORT {','.join(ip_parts)},{p1},{p2}"
            ctrl_sock.sendall((port_cmd + "\r\n").encode())
            reply = recv_reply(ctrl_sock)
            console.print(f"[STATUS] {port_cmd}")
            console.print(f"<<  {reply}")
            if reply.startswith("200"):
                transfer_mode = "ACTIVE"
                console.print(f"[STATUS] Active Mode BẬT — client lắng nghe UDP tại "
                      f"{client_ip}:{client_port}")
            else:
                active_sock.close()
                active_sock = None
                console.print("[ERROR] PORT command thất bại, giữ nguyên Passive Mode")
            continue

        if cmd == "PASV" and not args:
            if active_sock is not None:
                active_sock.close()
                active_sock = None
            transfer_mode = "PASV"
            console.print("[STATUS] Passive Mode BẬT (mặc định, tự động PASV mỗi lần truyền)")
            continue

        if cmd in ("STOR", "RETR", "LIST", "NLST", "APPE", "STOU"):
            if transfer_mode == "ACTIVE":
                data_addr = (host, SERVER_DATA_PORT)   # nơi CLIENT sẽ GỬI (STOR)
                recv_sock = active_sock                 # nơi CLIENT sẽ NHẬN (RETR/LIST/NLST)
            else:
                ctrl_sock.sendall(b"PASV\r\n")
                pasv_reply = recv_reply(ctrl_sock)
                console.print(f"<<  {pasv_reply}")
                pasv_addr = parse_pasv_reply(pasv_reply)
                if not pasv_addr:
                    console.print("[ERROR] PASV failed, aborting command")
                    continue
                data_addr = pasv_addr
                recv_sock = None

            if cmd == "STOR":
                local_path = Path(args)
                if not local_path.is_file():
                    console.print(f"[ERROR] Local file not found: {local_path}")
                    continue
                file_size = local_path.stat().st_size
                ctrl_sock.sendall(f"STOR {local_path.name}\r\n".encode())
                initial_reply = recv_reply(ctrl_sock)
                console.print(f"<<  {initial_reply}")

                if initial_reply.startswith("150"):
                    console.print(f"[STATUS] Uploading '{local_path.name}' ({file_size} bytes) ...")
                    bytes_sent = _send_file_udp(data_addr, local_path, simulate_faults=fault_injector)
                    final_reply = recv_reply(ctrl_sock)
                    console.print("[STATUS] Đang đối chiếu mã băm SHA-256 với Server...")
                    local_hash = sha256_file(local_path)
                    ctrl_sock.sendall(f"HASH {local_path.name}\r\n".encode())
                    hash_reply = recv_reply(ctrl_sock)
                    console.print(f"<<  {hash_reply}")
                    if hash_reply.startswith("213"):
                        server_hash = hash_reply.split()[-1]
                        if local_hash == server_hash:
                            console.print(f"[VERIFIED]: SHA-256 khớp tuyệt đối! ({local_hash[:8]}...)")
                        else:
                            console.print(f"[ERROR]: Dữ liệu hỏng!\nClient: {local_hash}\nServer: {server_hash}")
                    
                    match = re.search(r'\((\d+)\s+bytes\)', final_reply)
                    if match:
                        server_bytes = int(match.group(1))
                        if server_bytes == file_size:
                            console.print(f"[STATUS] Upload complete and verified ({file_size} bytes).")
                        else:
                            console.print(f"[ERROR] UDP data loss! Sent {file_size} bytes, but server received {server_bytes}.")
                    else:
                        console.print(f"[STATUS] Upload complete — {bytes_sent} bytes sent")
                else:
                    console.print("[ERROR] Upload aborted.")

            elif cmd in ("APPE", "STOU"):
                local_path = Path(args)
                if not local_path.is_file():
                    console.print(f"[ERROR] Local file not found: {local_path}")
                    continue
                file_size = local_path.stat().st_size
                # STOU không cần tên file đích (server tự sinh tên duy nhất);
                # APPE cần tên file đích để nối thêm dữ liệu vào cuối.
                if cmd == "APPE":
                    ctrl_sock.sendall(f"APPE {args}\r\n".encode())
                else:
                    ctrl_sock.sendall(f"STOU {local_path.name}\r\n".encode())
                initial_reply = recv_reply(ctrl_sock)
                console.print(f"<<  {initial_reply}")

                if initial_reply.startswith("150"):
                    console.print(f"[STATUS] Sending '{local_path.name}' ({file_size} bytes) via {cmd} ...")
                    bytes_sent = _send_file_udp(data_addr, local_path, simulate_faults=fault_injector)
                    final_reply = recv_reply(ctrl_sock)
                    console.print(f"<<  {final_reply}")
                    console.print(f"[STATUS] {cmd} complete — {bytes_sent} bytes sent")
                else:
                    console.print(f"[ERROR] {cmd} aborted.")
            
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
                    if transfer_mode == "ACTIVE":
                        bytes_recv = _recv_file_on_socket(recv_sock, Path(out_path))
                    else:
                        bytes_recv = _recv_file_udp(data_addr, Path(out_path))
                    final_reply = recv_reply(ctrl_sock)
                    print(f"<<  {final_reply}")
                    
                    if bytes_recv > 0:
                        print("[STATUS] Comparing SHA-256 hash with the server...")
                        try:
                            local_hash = sha256_file(Path(out_path))
                            ctrl_sock.sendall(f"HASH {args}\r\n".encode())
                            hash_reply = recv_reply(ctrl_sock)
                            print(f"<<  {hash_reply}")
                            if hash_reply.startswith("213"):
                                server_hash = hash_reply.split()[-1]
                                if local_hash == server_hash:
                                    print(f"[VERIFIED]: SHA-256 matches perfectly! ({local_hash[:8]}...)")
                                else:
                                    print(f"[ERROR]: Data is corrupted!\nClient: {local_hash}\nServer: {server_hash}")
                        except Exception as e:
                            print(f"[ERROR] Cannot verify Hash: {e}")
                    else:
                        print("[ERROR] No data received (check server logs)")
                else:
                    print("[ERROR] Download aborted.")
            
            elif cmd in ("LIST", "NLST"):
                ctrl_sock.sendall((raw + "\r\n").encode())
                initial_reply = recv_reply(ctrl_sock)
                console.print(f"<<  {initial_reply}")
                
                if initial_reply.startswith("150") or initial_reply.startswith("125"):
                    if transfer_mode == "ACTIVE":
                        text = _recv_text_on_socket(recv_sock)
                    else:
                        text = _recv_text_udp(data_addr)
                    if text:
                        console.print(text, end="")
                        if not text.endswith('\n'):
                            console.print()
                    final_reply = recv_reply(ctrl_sock)
                    console.print(f"<<  {final_reply}")
            
            continue

        # All other commands
        ctrl_sock.sendall((raw + "\r\n").encode())
        reply = recv_reply(ctrl_sock)
        console.print(f"<<  {reply}")

        if cmd == "QUIT":
            console.print("[STATUS] Connection closed")
            break

    ctrl_sock.close()