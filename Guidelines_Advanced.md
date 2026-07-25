# Guidelines — Hybrid FTP (Python) — ADVANCED LEVEL

**Builds directly on top of `Guidelines_Basic.md`.** Assumes the Basic
control channel (`USER`/`PASS`/`QUIT`/`NOOP`/`PWD`/`STAT`/`SIZE`/`MDTM`/
`HELP`/`TYPE`) and the simple fixed UDP data channel already work.

Scope: match the **Advanced Level** criteria of the spec:

- Binary file handling (images, video, archives) without corruption.
- Directory navigation & tree support.
- Flexible operating modes: Active/Passive switching.
- Concurrency control: multi-threaded server with full session isolation.

Still **no custom RDT layer yet** (no ACKs/sequence numbers/retransmit) —
that's `Guidelines_Excellent.md`. The data channel here is still "best
effort" UDP, just used correctly for binary bytes and routed through
Active/Passive addressing.

---

## 1. Additions to Project Layout

```
hybrid-ftp/
├── server/
│   ├── ...                    # existing from Basic
│   └── command_handler.py     # extended with directory + PORT/PASV commands
└── ftp_root/
    └── (nested subfolders for testing directory navigation)
```

No new top-level modules needed yet — `rdt_*` files still come in the
Excellent stage.

---

## 2. Directory Navigation & Tree Support

### 2.1 Path safety helper (do this first)

Every directory/file command below must resolve paths through this guard
to prevent path traversal outside the session's sandbox:

```python
# server/session.py (add to Session, or a free function in command_handler.py)
def resolve_path(session, rel: str):
    candidate = (session.cwd / rel).resolve() if rel else session.cwd
    root = session.root.resolve()
    if root != candidate and root not in candidate.parents:
        return None   # reject — escapes sandbox
    return candidate
```

### 2.2 Commands

```python
@command("CWD")
def cmd_cwd(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    session.cwd = target
    return 250, "Directory changed"

@command("CDUP")
def cmd_cdup(session, args):
    parent = session.cwd.parent
    if session.root.resolve() not in parent.parents and parent != session.root.resolve():
        parent = session.root.resolve()   # clamp at sandbox root
    session.cwd = parent
    return 250, "Directory changed to parent"

@command("MKD")
def cmd_mkd(session, args):
    target = resolve_path(session, args)
    if target is None:
        return 550, "Invalid path"
    target.mkdir(parents=False, exist_ok=False)
    return 257, f'"{args}" directory created'

@command("RMD")
def cmd_rmd(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    target.rmdir()   # only removes empty directories, matches spec
    return 250, "Directory removed"

@command("LIST")
def cmd_list(session, args):
    target = resolve_path(session, args) if args else session.cwd
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    lines = []
    for entry in sorted(target.iterdir()):
        st = entry.stat()
        kind = "d" if entry.is_dir() else "-"
        lines.append(f"{kind} {st.st_size:>10} {entry.name}")
    send_over_data_channel(session, "\r\n".join(lines).encode())
    return 226, "Transfer complete"

@command("NLST")
def cmd_nlst(session, args):
    target = resolve_path(session, args) if args else session.cwd
    if target is None or not target.is_dir():
        return 550, "Directory unavailable"
    names = "\r\n".join(sorted(e.name for e in target.iterdir()))
    send_over_data_channel(session, names.encode())
    return 226, "Transfer complete"
```

### 2.3 File management commands

```python
@command("DELE")
def cmd_dele(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    target.unlink()
    return 250, "File deleted"

@command("RNFR")
def cmd_rnfr(session, args):
    target = resolve_path(session, args)
    if target is None or not target.exists():
        return 550, "File unavailable"
    session.rename_from = target
    return 350, "Ready for RNTO"

@command("RNTO")
def cmd_rnto(session, args):
    if session.rename_from is None:
        return 503, "Bad sequence of commands"
    new_target = resolve_path(session, args)
    if new_target is None:
        return 550, "Invalid destination"
    session.rename_from.rename(new_target)
    session.rename_from = None
    return 250, "Rename successful"

@command("APPE")
def cmd_appe(session, args):
    target = resolve_path(session, args)
    if target is None:
        return 550, "Invalid path"
    receive_over_data_channel_append(session, target)   # open with "ab" mode
    return 226, "Transfer complete"

@command("STOU")
def cmd_stou(session, args):
    import uuid
    unique_name = f"{uuid.uuid4().hex}_{args or 'file'}"
    target = session.cwd / unique_name
    receive_over_data_channel(session, target)
    return 226, f"Transfer complete, stored as {unique_name}"
```

Add `rename_from: Path | None = None` to the `Session` dataclass from
`Guidelines_Basic.md`.

---

## 3. Binary File Handling (`TYPE I`)

The data-channel send/receive helpers must always use binary file modes
regardless of `TYPE`, and must never decode/re-encode bytes:

```python
def read_chunks(path, chunk_size=1024):
    with open(path, "rb") as f:      # always binary at the socket layer
        while chunk := f.read(chunk_size):
            yield chunk

def receive_over_data_channel(session, target_path):
    with open(target_path, "wb") as f:
        while True:
            chunk = recv_next_datagram(session)
            if not chunk:
                break
            f.write(chunk)

def receive_over_data_channel_append(session, target_path):
    with open(target_path, "ab") as f:   # APPE: append instead of overwrite
        while True:
            chunk = recv_next_datagram(session)
            if not chunk:
                break
            f.write(chunk)
```

Verify correctness with a real binary asset (JPEG, ZIP, small MP4) and
confirm byte-for-byte equality after RETR:

```bash
sha256sum original.jpg downloaded.jpg   # must match
```

> This is still running over plain, unreliable UDP (from Basic Level).
> On localhost/LAN demos this is usually fine, but any real loss will
> corrupt a binary file silently at this stage — that's exactly the gap
> the Excellent-Level RDT layer closes.

---

## 4. Active (`PORT`) vs Passive (`PASV`) Mode

| | Active (`PORT`) | Passive (`PASV`) |
|---|---|---|
| Who opens the data socket | Client opens it, tells server via `PORT h1,h2,h3,h4,p1,p2` | Server opens it, tells client the port in its `227` reply |
| Who sends first | Server → client | Client → server |
| Firewall/NAT friendliness | Poor | Good |

```python
@command("PASV")
def cmd_pasv(session, args):
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.bind(("0.0.0.0", 0))          # ephemeral port
    _, port = data_sock.getsockname()
    session.pasv_sock = data_sock
    session.data_mode = "PASSIVE"
    ip_parts = server_ip.split(".")
    p1, p2 = port >> 8, port & 0xFF
    return 227, f"Entering Passive Mode ({','.join(ip_parts)},{p1},{p2})"

@command("PORT")
def cmd_port(session, args):
    parts = list(map(int, args.split(",")))
    ip = ".".join(map(str, parts[:4]))
    port = (parts[4] << 8) + parts[5]
    session.data_peer = (ip, port)
    session.data_mode = "ACTIVE"
    return 200, "PORT command successful"
```

Add to `Session`: `data_mode: str = "NONE"`, `data_peer: tuple | None =
None`, `pasv_sock: socket.socket | None = None`.

`send_over_data_channel` / `recv_next_datagram` should branch on
`session.data_mode` to decide whether they use `session.pasv_sock` (already
bound, wait for client to connect first) or open a fresh socket and target
`session.data_peer` (active mode, server sends first).

Test both modes explicitly — the spec requires "Active / Passive mode
switching **or automation**", but implementing both is safer for the Oral
Viva ("explain Active/Passive mode nuances" is an explicit rubric line).

---

## 5. Concurrency Control (Multi-threaded Server)

Upgrade the Basic-Level sequential `while True: session_worker(...)` loop
to spawn a thread per client:

```python
# server/main.py
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

Protect any shared state (connected-client table) with a lock:

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

```python
def session_worker(ctrl_sock, addr):
    session = Session(ctrl_sock=ctrl_sock, addr=addr, root=Path("./ftp_root").resolve())
    session_id = id(session)
    register_client(session_id, {"addr": addr, "status": "connected"})
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
        unregister_client(session_id)
        ctrl_sock.close()
```

**Each session must own its own UDP data socket** (bound to a fresh
ephemeral port via `PASV`, or a fresh socket targeting `session.data_peer`
for `PORT`) so concurrent clients' data transfers never collide on the same
port.

> Note: Python's GIL means threads don't run CPU-bound work in true
> parallel, but this workload is I/O-bound (socket recv/send), so
> `threading` is adequate and easier to defend in the Oral Viva than
> `asyncio` or `multiprocessing`. Be ready to explain this trade-off if
> asked ("why threading and not multiprocessing?").

---

## 6. Testing Checklist (Advanced Level)

1. Nested directory tree exists under `ftp_root/`; `CWD`, `CDUP`, `LIST`,
   `MKD`, `RMD` all navigate/manage it correctly.
2. `RNFR` + `RNTO` renames a file; `RNTO` without a prior `RNFR` returns
   `503`.
3. `DELE` removes a file; deleting a non-existent file returns `550`.
4. Binary file (image/archive) round-trips byte-for-byte via `RETR`/`STOR`
   under `TYPE I`.
5. `PASV` and `PORT` both successfully complete at least one transfer each.
6. Two clients connect **simultaneously**, each does an independent
   upload/download — confirm no session cross-talk (check logs, check no
   file corruption from concurrent access).
7. Path traversal attempt (e.g. `CWD ../../etc`) is rejected with `550`.

---

## 7. Report Mapping (Advanced-Level portion)

| Report section | Comes from |
|---|---|
| 1. Protocol Interaction | Add PORT/PASV negotiation to the sequence diagram from Basic |
| 3. Flowcharts | Server thread-dispatch logic (§5), Active/Passive mode toggle (§4) |
| 4. Task Assignment Matrix | Split: directory ops owner, PORT/PASV owner, concurrency owner |
| 7. Demo Evidence | Screenshot: connected-client table with 2+ active sessions (§6 test 6) |

---

## 8. What's Still Deliberately Out of Scope

Move to `Guidelines_Excellent.md`:

- Custom UDP header with sequence numbers/ACKs/checksum/flags
- Retransmission on timeout, duplicate elimination, correct ordering under
  real loss
- Sliding window / congestion control
- `HASH` (MD5/SHA-256) end-to-end integrity verification
- Dynamic RTO estimation
