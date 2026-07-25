# Guidelines — Hybrid FTP (Python) — BASIC LEVEL

Scope: get a minimal but fully working client–server FTP up, matching the
**Basic Level** criteria of the spec:

- Basic user identification and access verification.
- ASCII text file handling.
- Upload and download of a single file.
- Single, fixed data-channel connection mechanism (no Active/Passive
  switching yet, no reliability layer yet — that comes in
  `Guidelines_Advanced.md` and `Guidelines_Excellent.md`).

Do not build multi-threading, binary support, or the custom RDT layer here
— keep this stage deliberately simple so you have a working baseline to
build on and to fall back to if later stages break.

---

## 1. Project Layout (starting point)

```
hybrid-ftp/
├── requirements.txt          # empty for now (stdlib only)
├── common/
│   ├── __init__.py
│   └── protocol.py           # command constants, reply-code helpers
├── server/
│   ├── __init__.py
│   ├── main.py                # entry point, listen socket
│   ├── session.py             # Session dataclass
│   └── command_handler.py     # dispatch table for basic commands
├── client/
│   ├── __init__.py
│   ├── main.py                # CLI entry point
│   └── cli.py                 # command loop
└── ftp_root/                  # sandbox directory served by the server
```

You'll add `rdt_packet.py`, `rdt_sender.py`, `rdt_receiver.py`, and
`hashutil.py` in the later stages — no need to create them yet.

---

## 2. TCP Control Channel

### 2.1 Server socket setup (single client at a time, no threads yet)

```python
# server/main.py
import socket

def run_server(host="0.0.0.0", port=2121):
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind((host, port))
    listen_sock.listen(5)
    print(f"FTP server listening on {host}:{port}")

    while True:
        client_sock, client_addr = listen_sock.accept()
        session_worker(client_sock, client_addr)   # handled sequentially, no threads yet
```

```python
# client/main.py
import socket

ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ctrl_sock.connect((server_host, server_port))
```

> Multi-threading (accepting new clients while one is still connected) is
> an **Advanced Level** requirement — see `Guidelines_Advanced.md`. For now
> it's fine for the server to serve one client, finish that session, then
> accept the next.

### 2.2 Wire format for commands and replies

Plain text lines terminated by `\r\n`:

```python
# common/protocol.py
def format_reply(code: int, text: str) -> bytes:
    return f"{code} {text}\r\n".encode("utf-8")

def parse_command(line: str) -> tuple[str, str]:
    line = line.strip("\r\n")
    if " " in line:
        cmd, args = line.split(" ", 1)
    else:
        cmd, args = line, ""
    return cmd.upper(), args
```

Reading a full line from a TCP socket needs manual buffering, since
`recv()` returns whatever bytes happen to be available:

```python
def read_line(sock: socket.socket, buf: bytearray) -> str | None:
    while b"\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return None          # connection closed
        buf.extend(chunk)
    line, _, rest = bytes(buf).partition(b"\r\n")
    buf.clear()
    buf.extend(rest)
    return line.decode("utf-8", errors="replace")
```

### 2.3 Session state (minimal version)

```python
# server/session.py
from dataclasses import dataclass, field
from pathlib import Path
import socket

@dataclass
class Session:
    ctrl_sock: socket.socket
    addr: tuple[str, int]
    root: Path                       # sandbox root for this session
    cwd: Path = field(default_factory=lambda: Path("/"))
    username: str = ""
    authenticated: bool = False
```

### 2.4 Command dispatch table

```python
# server/command_handler.py
from typing import Callable

Handler = Callable[["Session", str], tuple[int, str]]

COMMANDS: dict[str, Handler] = {}

def command(name: str):
    def deco(fn: Handler):
        COMMANDS[name] = fn
        return fn
    return deco

@command("USER")
def cmd_user(session, args):
    session.username = args
    return 331, "Username OK, need password"

@command("PASS")
def cmd_pass(session, args):
    if not session.username:
        return 503, "Bad sequence of commands"
    session.authenticated = check_credentials(session.username, args)
    return (230, "Login successful") if session.authenticated else (530, "Not logged in")

@command("QUIT")
def cmd_quit(session, args):
    return 221, "Goodbye"

@command("NOOP")
def cmd_noop(session, args):
    return 200, "Command OK"

@command("PWD")
def cmd_pwd(session, args):
    return 257, f'"{session.cwd}" is the current directory'

def dispatch(session, cmd: str, args: str) -> tuple[int, str]:
    handler = COMMANDS.get(cmd)
    if handler is None:
        return 502, "Command not implemented"
    return handler(session, args)
```

### 2.5 Basic info commands

```python
@command("STAT")
def cmd_stat(session, args):
    if args:
        target = session.cwd / args
        if not target.exists():
            return 450, "File unavailable"
        return 213, f"{target.name} {target.stat().st_size} bytes"
    return 211, "Server status OK"

@command("SIZE")
def cmd_size(session, args):
    target = session.cwd / args
    if not target.is_file():
        return 550, "File unavailable"
    return 213, str(target.stat().st_size)

@command("MDTM")
def cmd_mdtm(session, args):
    import datetime
    target = session.cwd / args
    if not target.is_file():
        return 550, "File unavailable"
    ts = datetime.datetime.fromtimestamp(target.stat().st_mtime)
    return 213, ts.strftime("%Y%m%d%H%M%S")

@command("HELP")
def cmd_help(session, args):
    if args and args.upper() in COMMANDS:
        return 214, f"Syntax for {args.upper()}: see project spec"
    return 214, "Commands: " + " ".join(sorted(COMMANDS.keys()))
```

### 2.6 Session loop

```python
def session_worker(ctrl_sock, addr):
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=Path("./ftp_root").resolve())
    ctrl_sock.sendall(format_reply(220, "Service ready"))
    buf = bytearray()
    while True:
        line = read_line(ctrl_sock, buf)
        if line is None:
            break
        cmd, args = parse_command(line)
        code, text = dispatch(session, cmd, args)
        ctrl_sock.sendall(format_reply(code, text))
        if cmd == "QUIT":
            break
    ctrl_sock.close()
```

---

## 3. UDP Data Channel (fixed, no Active/Passive switching, no RDT yet)

At Basic Level the data channel just needs to exist and move bytes — a
single fixed mechanism, no mode switching, no reliability engineering.

```python
# server: open a fixed, known data socket for the whole session
def open_data_channel(session, data_port=2122):
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.bind(("0.0.0.0", data_port))
    return data_sock
```

```python
@command("RETR")
def cmd_retr(session, args):
    target = session.cwd / args
    if not target.is_file():
        return 550, "File unavailable"
    data_sock = open_data_channel(session)
    client_data_addr = (session.addr[0], data_port_agreed_out_of_band)
    with open(target, "rb") as f:
        while chunk := f.read(1024):
            data_sock.sendto(chunk, client_data_addr)
    data_sock.sendto(b"", client_data_addr)   # empty datagram signals EOF (simplification)
    data_sock.close()
    return 226, "Transfer complete"

@command("STOR")
def cmd_stor(session, args):
    target = session.cwd / args
    data_sock = open_data_channel(session)
    with open(target, "wb") as f:
        while True:
            chunk, _ = data_sock.recvfrom(2048)
            if not chunk:
                break
            f.write(chunk)
    data_sock.close()
    return 226, "Transfer complete"
```

> This is intentionally simple and **not reliable** — no sequence numbers,
> no ACKs, no loss recovery. That is expected at Basic Level. Since UDP
> loss on `localhost` during a demo is rare, this will work for the grading
> demo but is not production-safe — the whole point of the Excellent Level
> stage is to replace this with the custom RDT layer.

---

## 4. `TYPE` command (declare ASCII mode)

```python
@command("TYPE")
def cmd_type(session, args):
    if args.upper() not in ("A", "I"):
        return 504, "Command not implemented for that parameter"
    session.type_ = args.upper()
    return 200, f"Type set to {session.type_}"
```

At this stage only `TYPE A` needs to actually work end-to-end (ASCII text
files); `TYPE I` can be accepted but binary correctness is an **Advanced
Level** requirement.

---

## 5. Client CLI (minimal)

```python
# client/cli.py
import socket

def run_client(host, port):
    ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ctrl_sock.connect((host, port))
    print(recv_reply(ctrl_sock))

    while True:
        line = input("ftp> ").strip()
        if not line:
            continue
        ctrl_sock.sendall((line + "\r\n").encode())
        print(recv_reply(ctrl_sock))
        if line.upper() == "QUIT":
            break
    ctrl_sock.close()

def recv_reply(sock) -> str:
    buf = bytearray()
    while b"\r\n" not in buf:
        buf.extend(sock.recv(4096))
    return buf.decode(errors="replace").strip()
```

Print connection state and transfer progress to the console — the spec
requires "a clear CLI or GUI" reporting network state even at Basic Level:

```python
print(f"[STATUS] Connected to {host}:{port}")
print(f"[STATUS] Uploading {filename} ... done ({size} bytes)")
```

---

## 6. Testing Checklist (Basic Level)

1. Server starts, accepts one connection, replies `220`.
2. `USER`/`PASS` round-trip returns `331` then `230` (or `530` on bad
   credentials).
3. `PWD`, `NOOP`, `HELP`, `STAT` return sensible replies.
4. Upload one small ASCII `.txt` file (`STOR`), confirm it lands correctly
   on the server (`diff`).
5. Download it back (`RETR`), confirm the round-tripped file is identical.
6. `QUIT` cleanly closes the control connection.

---

## 7. What's Deliberately Out of Scope Here

Move these to `Guidelines_Advanced.md` / `Guidelines_Excellent.md`:

- Binary file handling (`Advanced`)
- Directory navigation (`CWD`, `MKD`, `RMD`, `LIST`, `NLST`) (`Advanced`)
- `PORT`/`PASV` Active/Passive mode switching (`Advanced`)
- Multi-threaded/concurrent server (`Advanced`)
- Custom RDT header, ACKs, timeout/retransmit, sliding window (`Excellent`)
- `HASH`/SHA-256 integrity verification (`Excellent`)

---

## 8. Report Mapping (Basic-Level portion)

| Report section | Comes from |
|---|---|
| 2. Data Structures | `Session` dataclass (§2.3), `COMMANDS` dispatch dict (§2.4) |
| 3. Flowcharts | Simple session loop (§2.6) — no thread-dispatch or RDT states yet |
| 7. Demo Evidence | One successful upload + one download screenshot (§6) |
