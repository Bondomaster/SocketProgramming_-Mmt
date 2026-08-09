"""
common/protocol.py
==================
Wire-format helpers for the Hybrid FTP control channel (TCP).

All FTP commands and replies are plain text lines terminated by \\r\\n.
"""

import socket


def format_reply(code: int, text: str) -> bytes:
    """Encode an FTP reply line, e.g.  220 Service ready\\r\\n"""
    return f"{code} {text}\r\n".encode("utf-8")


def parse_command(line: str) -> tuple[str, str]:
    """
    Split a raw command line into (COMMAND, args).
    Leading/trailing \\r\\n are stripped; the command is uppercased.

    Examples
    --------
    >>> parse_command("USER alice")
    ('USER', 'alice')
    >>> parse_command("QUIT")
    ('QUIT', '')
    """
    line = line.strip("\r\n").strip()
    if " " in line:
        cmd, args = line.split(" ", 1)
    else:
        cmd, args = line, ""
    return cmd.upper(), args


def read_line(sock: socket.socket, buf: bytearray) -> str | None:
    """
    Read one complete \\r\\n-terminated line from *sock*, using *buf* as a
    persistent receive buffer across calls.

    Returns the decoded line (without the trailing \\r\\n), or None if the
    connection was closed by the remote end.

    Usage
    -----
    buf = bytearray()
    while True:
        line = read_line(sock, buf)
        if line is None:
            break          # peer closed
        cmd, args = parse_command(line)
        ...
    """
    while b"\r\n" not in buf:
        try:
            chunk = sock.recv(4096)
        except OSError:
            return None
        if not chunk:
            return None          # connection closed
        buf.extend(chunk)

    line, _, rest = bytes(buf).partition(b"\r\n")
    buf.clear()
    buf.extend(rest)
    return line.decode("utf-8", errors="replace")


def recv_reply(sock: socket.socket) -> str:
    """
    Read one complete FTP reply from *sock*.
    Suitable for the client side where we only need one reply at a time.
    Returns the decoded reply string (stripped).
    """
    buf = bytearray()
    while b"\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
    return buf.decode("utf-8", errors="replace").strip()
