# Guidelines — Hybrid FTP Application (Python)

Implementation guide for the *Design and Implementation of the Hybrid FTP*
lab project using **Python 3** and its built-in `socket` module only (no
third-party FTP/transfer libraries — matches the "native, low-level socket
APIs bundled with the language runtime" requirement).

Architecture recap: **TCP control channel** (commands, replies) + **UDP data
channel** (file payload) with a **custom reliability layer built on raw
UDP**, entirely hand-written.

---

## 1. Project Layout

```
hybrid-ftp/
├── requirements.txt          # empty, or dev-only (pytest) — no runtime deps
├── common/
│   ├── __init__.py
│   ├── protocol.py           # command constants, reply-code helpers
│   ├── rdt_packet.py         # RdtHeader (struct-based) pack/unpack + checksum
│   └── hashutil.py           # SHA-256 file hashing (hashlib, stdlib)
├── server/
│   ├── __init__.py
│   ├── main.py                # entry point, listen socket, thread-per-client
│   ├── session.py             # Session dataclass + per-client state
│   ├── command_handler.py     # dispatch table for FTP commands
│   ├── rdt_sender.py          # reliable UDP send (used for RETR/downloads)
│   └── rdt_receiver.py        # reliable UDP receive (used for STOR/uploads)
├── client/
│   ├── __init__.py
│   ├── main.py                # CLI entry point
│   ├── cli.py                 # command loop, argument parsing
│   ├── rdt_sender.py
│   └── rdt_receiver.py
└── tests/
    └── test_rdt.py            # loopback loss/corruption simulation
```

`common/rdt_packet.py`, `hashutil.py`, and the RDT sender/receiver logic are
symmetric between client and server — import them from `common/` (or a
shared `rdt/` package) instead of duplicating code.

---

## 2. Control Channel (TCP) Design

### 2.1 Socket setup

```python
# server/main.py
import socket
import threading

def run_server(host="0.0.0.0", port=2121):
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind((host, port))
    listen_sock.listen(16)
    print(f"FTP server listening on {host}:{port}")

    while True:
        client_sock, client_addr = listen_sock.accept()
        t = threading.Thread(target=session_worker, args=(client_sock, client_addr), daemon=True)
        t.start()
```

```python
# client/main.py
import socket

ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ctrl_sock.connect((server_host, server_port))
```

Each accepted TCP connection runs in its own `threading.Thread` so sessions
are fully isolated (Advanced-level "Concurrency Control"). Protect any
server-wide shared state (e.g. the connected-client table) with a
`threading.Lock`.

> Note on Python threading: the GIL means threads won't run true CPU-bound
> code in parallel, but this project is I/O-bound (socket reads/writes), so
> `threading` is perfectly adequate and simpler to defend in the Oral Viva
> than `asyncio` or `multiprocessing`. If you want to demonstrate deeper
> understanding, `asyncio` with `asyncio.start_server` + a UDP transport is
> a valid alternative — just be ready to explain the event loop model.

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

Reading a full line from a TCP socket requires your own buffering, since
`recv()` gives you whatever bytes are currently available:

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

Keep one `bytearray` buffer per session and reuse `read_line` in the
command loop.

### 2.3 Command dispatch table

```python
# server/command_handler.py
from typing import Callable

Handler = Callable[["Session", str], tuple[int, str]]  # returns (code, text)

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

def dispatch(session, cmd: str, args: str) -> tuple[int, str]:
    handler = COMMANDS.get(cmd)
    if handler is None:
        return 502, "Command not implemented"
    return handler(session, args)
```

The `@command("...")` decorator pattern keeps every handler in its own
function while still building a flat dispatch table — easy to extend and
easy to point to individual functions during the Live Coding portion of the
Oral Viva.

### 2.4 Session state

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
    type_: str = "A"                 # 'A' ASCII or 'I' Image/Binary
    mode: str = "S"                  # Stream / Block / Compressed
    data_mode: str = "NONE"          # "NONE" | "ACTIVE" | "PASSIVE"
    data_peer: tuple[str, int] | None = None     # for PORT (active)
    pasv_sock: socket.socket | None = None        # for PASV (passive)
    rename_from: Path | None = None
```

### 2.5 Authentication and state-machine notes

- `USER` before `PASS` → `331`; `PASS` without a prior `USER` → `503`.
- Commands requiring login (`LIST`, `RETR`, `STOR`, …) must check
  `session.authenticated` and return `530 Not logged in` otherwise.
- `RNFR` sets `session.rename_from` and replies `350`; a subsequent `RNTO`
  performs the rename and clears the field; `RNTO` without a preceding
  `RNFR` → `503`.
- **Always resolve paths against `session.root`** and reject anything that
  escapes it:

```python
def resolve_path(session: Session, rel: str) -> Path | None:
    candidate = (session.cwd / rel).resolve()
    root = session.root.resolve()
    if root not in candidate.parents and candidate != root:
        return None   # path traversal attempt — reject
    return candidate
```

---

## 3. Data Channel (UDP) Design

### 3.1 Active vs Passive mode

| | Active (`PORT`) | Passive (`PASV`) |
|---|---|---|
| Who opens the data socket | Client opens it, tells server via `PORT h1,h2,h3,h4,p1,p2` | Server opens it, tells client the port in its `227` reply |
| Who sends first | Server → client | Client → server |
| Firewall/NAT friendliness | Poor | Good |

```python
# server: PASV handler
def cmd_pasv(session, args):
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.bind(("0.0.0.0", 0))          # ephemeral port
    _, port = data_sock.getsockname()
    session.pasv_sock = data_sock
    session.data_mode = "PASSIVE"
    ip_parts = server_ip.split(".")
    p1, p2 = port >> 8, port & 0xFF
    return 227, f"Entering Passive Mode ({','.join(ip_parts)},{p1},{p2})"

# server: PORT handler (active)
def cmd_port(session, args):
    parts = list(map(int, args.split(",")))
    ip = ".".join(map(str, parts[:4]))
    port = (parts[4] << 8) + parts[5]
    session.data_peer = (ip, port)
    session.data_mode = "ACTIVE"
    return 200, "PORT command successful"
```

Implement both — default to Passive for the Advanced level, but keep Active
working since `PORT` is part of the required command coverage.

### 3.2 Custom UDP header (RDT layer)

Define the header with `struct` for exact, portable byte layout:

```python
# common/rdt_packet.py
import struct
import zlib

# ! = network byte order (big-endian), standard sizes
# I  = uint32 (seq_num), I = uint32 (ack_num), H = uint16 (flags),
# H  = uint16 (length),  I = uint32 (checksum)
HEADER_FORMAT = "!IIHHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)   # 16 bytes

FLAG_ACK = 1 << 0
FLAG_SYN = 1 << 1
FLAG_FIN = 1 << 2
FLAG_NAK = 1 << 3

def pack_packet(seq_num: int, ack_num: int, flags: int, payload: bytes) -> bytes:
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), 0)
    checksum = zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), checksum)
    return header + payload

def unpack_packet(data: bytes) -> tuple[int, int, int, bytes, bool]:
    """Returns (seq_num, ack_num, flags, payload, checksum_ok)."""
    seq_num, ack_num, flags, length, checksum = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:HEADER_SIZE + length]
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, length, 0)
    ok = (zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF) == checksum
    return seq_num, ack_num, flags, payload, ok
```

`zlib.crc32` is part of the Python standard library, so it is fair game
even under a "no third-party libraries" rule — but confirm with your
instructor if any use of `zlib`/`hashlib` counts as "external" for this
assignment (see Section 6).

### 3.3 Choosing an RDT algorithm

| Algorithm | Complexity | Fits which level |
|---|---|---|
| Stop-and-Wait | Low | Excellent (baseline) |
| Go-Back-N | Medium | Excellent (better throughput) |
| Selective Repeat | High | Excellent (best throughput, most defensible in viva) |

**Recommendation:** **Selective Repeat with a sliding window** — satisfies
"Sliding Window" congestion control and gives the most to discuss in the
Oral Viva.

### 3.4 Sender (pseudocode → real Python using `selectors`)

Python's blocking `recvfrom()` has no built-in per-call timeout unless you
either set a socket timeout or use `selectors`/`select`. Prefer `selectors`
so you can wait on the UDP socket with a deadline without blocking forever:

```python
# common/rdt_sender.py
import time
import selectors
from .rdt_packet import pack_packet, unpack_packet, FLAG_FIN, FLAG_ACK

MSS = 1024            # payload bytes per packet
WINDOW = 8             # sliding window size
RTO = 0.4              # seconds, fixed baseline (see Section 3.6 for dynamic RTO)

def send_file(sock, dest_addr, chunks: list[bytes]):
    sel = selectors.DefaultSelector()
    sel.register(sock, selectors.EVENT_READ)

    base = 0
    next_seq = 0
    timers: dict[int, float] = {}
    acked: set[int] = set()
    total = len(chunks)

    def send_chunk(seq):
        pkt = pack_packet(seq, 0, 0, chunks[seq])
        sock.sendto(pkt, dest_addr)
        timers[seq] = time.monotonic() + RTO

    while base < total:
        while next_seq < total and next_seq < base + WINDOW:
            send_chunk(next_seq)
            next_seq += 1

        soonest = min(timers.values()) if timers else time.monotonic() + RTO
        timeout = max(0, soonest - time.monotonic())
        events = sel.select(timeout=timeout)

        if events:
            data, _ = sock.recvfrom(2048)
            seq, ack_num, flags, _, ok = unpack_packet(data)
            if ok and (flags & FLAG_ACK):
                timers.pop(ack_num, None)
                acked.add(ack_num)
                while base in acked:
                    acked.discard(base)
                    base += 1
        else:
            # timeout: resend every packet whose timer has expired (Selective Repeat)
            now = time.monotonic()
            for seq, deadline in list(timers.items()):
                if deadline <= now:
                    send_chunk(seq)

    fin_pkt = pack_packet(next_seq, 0, FLAG_FIN, b"")
    sock.sendto(fin_pkt, dest_addr)
    sel.close()
```

### 3.5 Receiver (pseudocode → real Python)

```python
# common/rdt_receiver.py
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN

RECV_WINDOW = 8

def recv_file(sock, out_path):
    buffer: dict[int, bytes] = {}
    expected = 0
    peer_addr = None

    with open(out_path, "wb") as out:
        while True:
            data, addr = sock.recvfrom(2048)
            peer_addr = peer_addr or addr
            seq, _, flags, payload, ok = unpack_packet(data)
            if not ok:
                continue   # drop corrupted packet silently; sender retransmits on timeout

            if flags & FLAG_FIN and seq == expected:
                _flush_in_order(buffer, out, expected)
                ack = pack_packet(0, expected, FLAG_ACK, b"")
                sock.sendto(ack, peer_addr)
                break

            if expected <= seq < expected + RECV_WINDOW:
                buffer[seq] = payload    # dict keyed by seq -> automatic dedup

            while expected in buffer:
                out.write(buffer.pop(expected))
                expected += 1

            ack = pack_packet(0, expected, FLAG_ACK, b"")
            sock.sendto(ack, peer_addr)

def _flush_in_order(buffer, out, expected):
    while expected in buffer:
        out.write(buffer.pop(expected))
        expected += 1
```

Key correctness properties this design gives you, mapped to the spec:
- **Zero packet loss** → timers + retransmission (Section 3.4).
- **Corruption detection** → CRC32 checksum, drop on mismatch.
- **Duplicate elimination** → `dict` keyed by `seq_num` naturally ignores
  re-received duplicates.
- **Correct ordering** → data is written to disk only when contiguous from
  `expected`; out-of-order packets are buffered, not discarded.

### 3.6 Timers and dynamic RTO (optional, Excellent-level polish)

```python
estimated_rtt = 0.0
dev_rtt = 0.0
rto = 0.4  # seconds, initial

def update_rto(sample_rtt: float):
    global estimated_rtt, dev_rtt, rto
    estimated_rtt = 0.875 * estimated_rtt + 0.125 * sample_rtt
    dev_rtt = 0.75 * dev_rtt + 0.25 * abs(sample_rtt - estimated_rtt)
    rto = estimated_rtt + 4 * dev_rtt
```

### 3.7 Flow / congestion control

Treat `WINDOW` as a `cwnd`-like value:

```python
cwnd = 2          # start small (slow start)
ssthresh = 16

def on_full_window_success():
    global cwnd
    if cwnd < ssthresh:
        cwnd *= 2                       # slow start: exponential growth
    else:
        cwnd += 1                       # congestion avoidance: linear growth

def on_timeout():
    global cwnd, ssthresh
    ssthresh = max(cwnd // 2, 2)
    cwnd = 2                            # back off hard on loss
```

Wire this into the sender loop from Section 3.4 by replacing the fixed
`WINDOW` constant with the mutable `cwnd` value.

---

## 4. Binary vs ASCII Transfer (`TYPE`)

- `TYPE A` (ASCII): read the file in text-safe chunks; document your
  line-ending policy if you care about cross-platform interop (most
  implementations treat A and I identically on Linux/macOS).
- `TYPE I` (Image/Binary): always open with `"rb"`/`"wb"` — **never**
  decode/re-encode the byte stream. This is what images/video/archives use.

```python
def read_chunks(path, chunk_size=1024):
    with open(path, "rb") as f:     # always binary at the socket layer
        while chunk := f.read(chunk_size):
            yield chunk
```

Chunk file reads rather than loading whole files into memory — matters for
large binaries and shows good engineering judgment in review.

---

## 5. Directory & File Operations

Use `pathlib`:

```python
@command("LIST")
def cmd_list(session, args):
    target = resolve_path(session, args) if args else session.cwd
    if target is None:
        return 550, "File unavailable"
    lines = []
    for entry in sorted(target.iterdir()):
        st = entry.stat()
        kind = "d" if entry.is_dir() else "-"
        lines.append(f"{kind} {st.st_size:>10} {entry.name}")
    send_over_data_channel(session, "\r\n".join(lines).encode())
    return 226, "Transfer complete"
```

`SIZE`, `MDTM`, `DELE`, `RNFR`/`RNTO`, `MKD`, `RMD`, `CWD`, `CDUP`, `PWD` are
straightforward `pathlib` wrappers — always run every path through
`resolve_path()` (Section 2.5) before touching the filesystem.

```python
@command("DELE")
def cmd_dele(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    target.unlink()
    return 250, "File deleted"
```

Decide explicitly whether `LIST`/`NLST` results go over the **data channel**
(matches RFC 959) or directly over the control TCP socket as a
simplification — either is acceptable if documented in the report.

---

## 6. Data Integrity Verification (`HASH`)

```python
# common/hashutil.py
import hashlib

def sha256_file(path, chunk_size=8192) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
```

`hashlib` is part of the Python standard library, so no third-party
dependency is introduced — this sidesteps the "no external library"
ambiguity that C/C++ teams run into with OpenSSL. Still worth a one-line
mention in the report that `hashlib` is stdlib, not a banned FTP/transfer
framework.

```python
@command("HASH")
def cmd_hash(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    digest = sha256_file(target)
    return 213, f"SHA-256 {digest}"
```

Client workflow: `HASH filename` before/after transfer, compare digests
locally, print match/mismatch — this is the screenshot needed for Demo
Evidence (Section 2.4, item 7 of the spec).

---

## 7. Concurrency Model

- **Server:** one `threading.Thread` per accepted TCP control connection.
  Each session opens its **own** UDP data socket (Section 3.1) so
  concurrent clients never collide.
- **Shared state** (connected-client table) protected by a
  `threading.Lock`, held only around the dict mutation:

```python
clients_lock = threading.Lock()
clients: dict[int, dict] = {}

def register_client(session_id, info):
    with clients_lock:
        clients[session_id] = info

def unregister_client(session_id):
    with clients_lock:
        clients.pop(session_id, None)
```

- **Client:** can remain single-threaded (one control connection, one
  transfer at a time). If you want a responsive CLI during large
  transfers, run the transfer in a worker thread and `.join()` it before
  sending the next control command.

```python
def session_worker(ctrl_sock, addr):
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=Path("./ftp_root").resolve())
    register_client(id(session), {"addr": addr, "status": "connected"})
    ctrl_sock.sendall(format_reply(220, "Service ready"))
    buf = bytearray()
    try:
        while True:
            line = read_line(ctrl_sock, buf)
            if line is None:
                break
            cmd, args = parse_command(line)
            code, text = dispatch(session, cmd, args)
            ctrl_sock.sendall(format_reply(code, text))
            if cmd == "QUIT":
                break
    finally:
        unregister_client(id(session))
        ctrl_sock.close()
```

---

## 8. CLI / Logging Requirements

Minimum server-side logging (use the stdlib `logging` module rather than
bare `print`, so you can control verbosity for the demo):

```python
import logging
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ftp-server")

log.info("CONNECT %s:%s (session #%s)", addr[0], addr[1], session_id)
log.info("CMD %s %s", cmd, args)
log.info("XFER STOR %s [%3d%%] %d bytes, %.1fs, %d retransmits",
          filename, pct, size, elapsed, retransmit_count)
log.info("DISCONNECT %s:%s", addr[0], addr[1])
```

Keep the `clients` dict (Section 7) printable on demand — e.g. a `SESSIONS`
console command that dumps it — for the Demo Evidence screenshot and to
prove concurrency in the Oral Viva.

---

## 9. Testing Checklist (maps to Section 4.5 of the spec)

1. `python3 -m server.main` runs cleanly on a fresh checkout, no missing
   stdlib-only imports.
2. ASCII upload + download round-trip, byte-for-byte identical
   (`diff`/`filecmp.cmp`).
3. Binary upload + download (image/archive), byte-for-byte identical —
   verify with `HASH` and/or `hashlib.sha256` locally.
4. Two clients connected simultaneously, each doing an independent
   transfer — confirm no cross-talk in logs or file paths.
5. Simulate packet loss/corruption (Section 10) and confirm the RDT layer
   recovers with zero data loss.
6. Active mode and Passive mode both exercised for at least one transfer.
7. `ABOR` mid-transfer cleanly resets the data channel (no orphaned socket,
   no leftover partial file left in an inconsistent state).
8. Directory operations (`MKD`, `CWD`, `LIST`, `RMD`, `RNFR`/`RNTO`,
   `DELE`) verified against a real nested directory tree.

Use `pytest` for the RDT unit tests (loopback UDP sockets on `127.0.0.1`
with different ports simulate client/server without needing real network
conditions):

```python
# tests/test_rdt.py
import socket
from common.rdt_sender import send_file
from common.rdt_receiver import recv_file

def test_lossless_transfer(tmp_path):
    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", 0))
    dest = receiver_sock.getsockname()

    chunks = [b"x" * 1024 for _ in range(20)]
    out_file = tmp_path / "received.bin"

    import threading
    t = threading.Thread(target=recv_file, args=(receiver_sock, out_file))
    t.start()
    send_file(sender_sock, dest, chunks)
    t.join(timeout=5)

    assert out_file.read_bytes() == b"".join(chunks)
```

---

## 10. Simulating Loss/Corruption for the Demo

Don't rely on real network loss during a live demo. Add a debug-only hook
around `sendto()`:

```python
import random

SIMULATE_FAULTS = False   # flip via CLI flag, e.g. --drop-rate / --corrupt-rate
DROP_RATE = 0.0
CORRUPT_RATE = 0.0

def guarded_sendto(sock, data: bytes, addr):
    if SIMULATE_FAULTS:
        if random.random() < DROP_RATE:
            return                                   # pretend it never left
        if random.random() < CORRUPT_RATE:
            data = bytearray(data)
            data[16] ^= 0xFF                           # flip a byte in the payload
            data = bytes(data)
    sock.sendto(data, addr)
```

```python
# CLI flags (client/cli.py)
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--drop-rate", type=float, default=0.0)
parser.add_argument("--corrupt-rate", type=float, default=0.0)
```

Use this to demonstrate recovery live and explain exactly what's happening —
this directly supports the "Live Coding & On-the-Spot Debugging" rubric
criterion.

---

## 11. Mapping Work to the Technical Report (Section 2.4)

| Report section | Where it comes from in this codebase |
|---|---|
| 1. Protocol Interaction (sequence diagram) | Trace one session: TCP handshake → `USER`/`PASS` → `PASV`/`PORT` → UDP exchange (SYN/DATA*N/FIN+ACK) → `QUIT` |
| 2. Data Structures | `rdt_packet.HEADER_FORMAT` (§3.2), `Session` dataclass (§2.4), `COMMANDS` dispatch dict |
| 3. Flowcharts | `session_worker` loop (§7), sender/receiver state machines (§3.4–3.5), Active/Passive toggle (§3.1) |
| 4. Task Assignment Matrix | Split by module: control-channel/commands, RDT sender, RDT receiver, CLI/logging, testing |
| 5. Peer Evaluation | Each member should be able to defend the module(s) they own in the Oral Viva |
| 6. GenAI Log | Record prompts/output for anything AI-assisted (e.g. "generate a Selective Repeat sender in Python") plus the manual fixes made |
| 7. Demo Evidence | Screenshots from §8 (session log), §6 (HASH match), §9 test 4 (concurrent sessions) |

---

## 12. Environment / Run Instructions (reference `README` snippet)

```bash
# no third-party runtime dependencies — stdlib only
python3 --version   # 3.10+ recommended (for `match` statements / dataclasses / walrus)

# run server
python3 -m server.main --host 0.0.0.0 --port 2121 --root ./ftp_root

# run client
python3 -m client.main --host 127.0.0.1 --port 2121

# run tests
pip install pytest --break-system-packages   # dev-only, not a runtime dep
pytest tests/
```

---

## 13. Common Pitfalls (Python-specific)

- **`recv()`/`recvfrom()` returning partial data**: TCP is a byte stream —
  never assume one `recv()` call returns one full line/command; always
  buffer (Section 2.2).
- **Blocking `recvfrom()` with no timeout** will hang the sender forever on
  packet loss — always use `selectors`/`select` or `sock.settimeout(...)`
  so the retransmission timer can actually fire.
- **Mutable default arguments / shared global state across threads**
  without a `Lock` — Python dicts are *not* automatically thread-safe for
  compound operations (check-then-set), even though single operations are
  often atomic due to the GIL; don't rely on that as a design principle.
- **Datagram size vs MSS**: keep UDP payload chunks well under 1500 bytes
  (account for the 16-byte custom header) to avoid IP fragmentation, which
  defeats your own reliability assumptions.
- **Forgetting `struct` byte order**: always use `!` (network byte order,
  big-endian) in `HEADER_FORMAT` so client/server agree regardless of host
  architecture.
- **Not closing sockets on `ABOR`/exceptions**: wrap session handling in
  `try/finally` (Section 7) so a crashed transfer doesn't leak a UDP socket
  and freeze that session's data port for future data on the same port.
- **Reusing one UDP socket across multiple concurrent sessions** without
  per-session filtering — bind a fresh ephemeral-port socket per session
  (Section 3.1) rather than sharing one global data socket.

---

## 14. Suggested Build Order

1. TCP control channel skeleton: `USER`/`PASS`/`QUIT`/`NOOP`/`PWD` only,
   single-threaded, single client. Confirm reply codes look right.
2. Add `PASV`, minimal UDP echo (no reliability yet); confirm `STOR`/`RETR`
   of a small ASCII file works over plain UDP `sendto`/`recvfrom`.
3. Layer in `rdt_packet.py` + Stop-and-Wait; confirm correctness with
   simulated loss (Section 10).
4. Upgrade to Selective Repeat with a sliding window; add dynamic RTO.
5. Add binary `TYPE I` support, directory commands, rename, delete.
6. Add multi-threading + session table + logging.
7. Add `HASH`/SHA-256 integrity check end-to-end.
8. Add `ABOR`, `PORT`/Active mode, `APPE`, `STOU`, `HELP`.
9. Polish CLI, write `pytest` tests, run the full checklist (Section 9),
   then write the report.

---

*This guide is a technical roadmap, not a spec substitute — always
cross-check against the original project brief and course-specific
constraints (e.g. whether `hashlib`/`zlib` count as "external libraries" for
your course) before finalizing design decisions.*
