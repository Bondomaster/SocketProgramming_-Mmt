# Guidelines — Hybrid FTP (Python) — EXCELLENT LEVEL

**Builds directly on top of `Guidelines_Advanced.md`.** Assumes directory
navigation, binary handling, Active/Passive mode, and the multi-threaded
server already work. This stage **replaces** the plain "best effort" UDP
send/recv used so far with a real, custom reliability layer.

Scope: match the **Excellent Level** criteria of the spec:

- Custom Reliable UDP Layer (RDT): ACKs, sequence numbers,
  timeout/retransmit (Stop-and-Wait, Go-Back-N, or Selective Repeat).
- Congestion / Flow Control: Sliding Window or equivalent mechanism.
- Data Integrity Verification: end-to-end MD5/SHA-256 hash comparison.

---

## 1. Additions to Project Layout

```
hybrid-ftp/
├── common/
│   ├── rdt_packet.py          # RdtHeader pack/unpack + checksum   (NEW)
│   └── hashutil.py            # SHA-256 file hashing               (NEW)
├── server/
│   ├── rdt_sender.py          # reliable UDP send (RETR)            (NEW)
│   └── rdt_receiver.py        # reliable UDP receive (STOR)         (NEW)
├── client/
│   ├── rdt_sender.py
│   └── rdt_receiver.py
└── tests/
    └── test_rdt.py            # loopback loss/corruption simulation (NEW)
```

`rdt_packet.py`, `rdt_sender.py`, `rdt_receiver.py`, and `hashutil.py`
should be identical on both client and server sides — put them in
`common/` and import from both, rather than duplicating the files.

---

## 2. Custom UDP Header (RDT Layer)

Use `struct` for an exact, portable byte layout:

```python
# common/rdt_packet.py
import struct
import zlib

# ! = network byte order (big-endian)
# I = uint32 (seq_num), I = uint32 (ack_num), H = uint16 (flags),
# H = uint16 (length), I = uint32 (checksum)
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

`zlib.crc32` is Python stdlib — confirm with your instructor whether stdlib
`zlib`/`hashlib` count as "external libraries" for your course (they almost
certainly don't, since the ban targets FTP/transfer frameworks, not
general-purpose stdlib modules — but it's worth one sentence in the
report either way).

---

## 3. Choosing an RDT Algorithm

| Algorithm | Complexity | Notes |
|---|---|---|
| Stop-and-Wait | Low | Simplest to implement and explain; low throughput |
| Go-Back-N | Medium | Better throughput; retransmits from the lost packet onward |
| Selective Repeat | High | Best throughput; retransmits only the lost packet(s) |

**Recommendation: Selective Repeat with a sliding window** — it directly
satisfies the "Sliding Window" congestion-control requirement and gives you
the most concrete detail to defend in the Oral Viva (window size, per-packet
timers, buffer management). If time is tight, Stop-and-Wait is an
acceptable fallback that still earns full marks on the RDT criterion if
implemented correctly and explained well — throughput isn't graded, only
correctness and understanding.

---

## 4. Sender: Selective Repeat with Sliding Window

```python
# common/rdt_sender.py
import time
import selectors
from .rdt_packet import pack_packet, unpack_packet, FLAG_FIN, FLAG_ACK

MSS = 1024             # payload bytes per packet
INITIAL_WINDOW = 2      # slow-start style: start small
SSTHRESH = 16
INITIAL_RTO = 0.4       # seconds, before dynamic estimation kicks in

def send_file(sock, dest_addr, chunks: list[bytes], simulate_faults=None):
    sel = selectors.DefaultSelector()
    sel.register(sock, selectors.EVENT_READ)

    base = 0
    next_seq = 0
    cwnd = INITIAL_WINDOW
    ssthresh = SSTHRESH
    rto = INITIAL_RTO
    estimated_rtt = dev_rtt = 0.0
    timers: dict[int, float] = {}
    sent_at: dict[int, float] = {}
    acked: set[int] = set()
    total = len(chunks)
    retransmit_count = 0

    def send_chunk(seq):
        pkt = pack_packet(seq, 0, 0, chunks[seq])
        if simulate_faults:
            simulate_faults(sock, pkt, dest_addr)
        else:
            sock.sendto(pkt, dest_addr)
        timers[seq] = time.monotonic() + rto
        sent_at[seq] = time.monotonic()

    while base < total:
        while next_seq < total and next_seq < base + int(cwnd):
            send_chunk(next_seq)
            next_seq += 1

        soonest = min(timers.values()) if timers else time.monotonic() + rto
        timeout = max(0, soonest - time.monotonic())
        events = sel.select(timeout=timeout)

        if events:
            data, _ = sock.recvfrom(2048)
            _, ack_num, flags, _, ok = unpack_packet(data)
            if ok and (flags & FLAG_ACK) and ack_num in timers:
                sample_rtt = time.monotonic() - sent_at[ack_num]
                estimated_rtt = 0.875 * estimated_rtt + 0.125 * sample_rtt
                dev_rtt = 0.75 * dev_rtt + 0.25 * abs(sample_rtt - estimated_rtt)
                rto = max(0.05, estimated_rtt + 4 * dev_rtt) if estimated_rtt else INITIAL_RTO

                timers.pop(ack_num, None)
                acked.add(ack_num)
                while base in acked:
                    acked.discard(base)
                    base += 1

                # congestion control: slow start / congestion avoidance
                if cwnd < ssthresh:
                    cwnd += 1          # +1 per new ACK during slow start (simple variant)
                else:
                    cwnd += 1 / cwnd    # linear growth per RTT (congestion avoidance)
        else:
            # timeout: back off and resend every expired packet (Selective Repeat)
            ssthresh = max(cwnd / 2, 2)
            cwnd = INITIAL_WINDOW
            now = time.monotonic()
            for seq, deadline in list(timers.items()):
                if deadline <= now:
                    retransmit_count += 1
                    send_chunk(seq)

    fin_pkt = pack_packet(next_seq, 0, FLAG_FIN, b"")
    sock.sendto(fin_pkt, dest_addr)
    sel.close()
    return retransmit_count   # useful for logging (Section 8 of Advanced guide)
```

---

## 5. Receiver: Buffering, Dedup, and Ordering

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

            expected = _flush_in_order(buffer, out, expected)

            ack = pack_packet(0, expected, FLAG_ACK, b"")
            sock.sendto(ack, peer_addr)

def _flush_in_order(buffer, out, expected):
    while expected in buffer:
        out.write(buffer.pop(expected))
        expected += 1
    return expected
```

How this satisfies each spec requirement:
- **Zero packet loss** → sender-side timers + retransmission (§4).
- **Corruption detection** → CRC32 checksum in `unpack_packet`, drop on
  mismatch.
- **Duplicate elimination** → `dict` keyed by `seq_num` overwrites/ignores
  re-received duplicates automatically.
- **Correct ordering** → bytes are only written to disk once contiguous
  from `expected`; anything arriving out of order sits in `buffer` until
  the gap is filled.

---

## 6. Wiring RDT into `RETR` / `STOR`

Replace the Basic/Advanced-level plain `sendto`/`recvfrom` loops with calls
into the new reliable layer:

```python
@command("RETR")
def cmd_retr(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    chunks = list(read_chunks(target, chunk_size=MSS))
    data_sock = get_session_data_socket(session)   # from Active/Passive setup
    dest = get_session_data_peer(session)
    retransmits = send_file(data_sock, dest, chunks)
    log.info("XFER RETR %s, %d chunks, %d retransmits", args, len(chunks), retransmits)
    return 226, "Transfer complete"

@command("STOR")
def cmd_stor(session, args):
    target = resolve_path(session, args)
    if target is None:
        return 550, "Invalid path"
    data_sock = get_session_data_socket(session)
    recv_file(data_sock, target)
    return 226, "Transfer complete"
```

---

## 7. Data Integrity Verification (`HASH`)

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

```python
@command("HASH")
def cmd_hash(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    digest = sha256_file(target)
    return 213, f"SHA-256 {digest}"
```

Client-side workflow: request `HASH filename` **before** downloading and
again **after**, compare the two digests locally, print a clear
match/mismatch — this is the exact screenshot needed for Demo Evidence
(spec Section 2.4, item 7):

```python
before = get_hash_from_server(ctrl_sock, "photo.jpg")
retrieve_file(ctrl_sock, data_sock, "photo.jpg")
after = sha256_file(local_path("photo.jpg"))
print("MATCH" if before == after else "MISMATCH", before, after)
```

---

## 8. Simulating Loss/Corruption for the Live Demo

Real network loss is hard to reproduce reliably in front of examiners — add
a debug-only fault injector around the sender's `sendto`:

```python
import random

def make_fault_injector(drop_rate=0.0, corrupt_rate=0.0):
    def inject(sock, pkt: bytes, addr):
        if random.random() < drop_rate:
            return                                  # pretend it never left
        if random.random() < corrupt_rate:
            pkt = bytearray(pkt)
            pkt[16] ^= 0xFF                           # flip a byte in the payload
            pkt = bytes(pkt)
        sock.sendto(pkt, addr)
    return inject
```

```python
# CLI flags (client/cli.py)
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--drop-rate", type=float, default=0.0)
parser.add_argument("--corrupt-rate", type=float, default=0.0)
args = parser.parse_args()

fault_injector = make_fault_injector(args.drop_rate, args.corrupt_rate)
send_file(sock, dest, chunks, simulate_faults=fault_injector)
```

Use this live: set `--drop-rate 0.1`, upload a file, show the retransmit
count in the logs, then show `HASH` still matches — this maps directly onto
the "Live Coding & On-the-Spot Debugging" and "Optimised reliable UDP"
rubric lines.

---

## 9. Unit Testing the RDT Layer

```python
# tests/test_rdt.py
import socket
import threading
from common.rdt_sender import send_file
from common.rdt_receiver import recv_file

def test_lossless_transfer(tmp_path):
    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", 0))
    dest = receiver_sock.getsockname()

    chunks = [b"x" * 1024 for _ in range(20)]
    out_file = tmp_path / "received.bin"

    t = threading.Thread(target=recv_file, args=(receiver_sock, out_file))
    t.start()
    send_file(sender_sock, dest, chunks)
    t.join(timeout=5)

    assert out_file.read_bytes() == b"".join(chunks)

def test_recovers_from_loss(tmp_path):
    from common.rdt_sender import make_fault_injector  # or import directly if defined in sender module
    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", 0))
    dest = receiver_sock.getsockname()

    chunks = [bytes([i % 256]) * 1024 for i in range(30)]
    out_file = tmp_path / "received.bin"
    injector = make_fault_injector(drop_rate=0.15, corrupt_rate=0.05)

    t = threading.Thread(target=recv_file, args=(receiver_sock, out_file))
    t.start()
    send_file(sender_sock, dest, chunks, simulate_faults=injector)
    t.join(timeout=10)

    assert out_file.read_bytes() == b"".join(chunks)   # must still be exact despite loss/corruption
```

Run with:

```bash
pip install pytest --break-system-packages
pytest tests/test_rdt.py -v
```

---

## 10. Testing Checklist (Excellent Level)

1. Lossless transfer (Section 9, first test) passes reliably.
2. Transfer under simulated loss + corruption (Section 9, second test)
   still produces a byte-identical file — zero data loss.
3. `HASH` before/after a real transfer (through the actual server, not just
   the unit test) matches.
4. Retransmit count in the logs is > 0 when `--drop-rate` is set, and 0
   when it isn't — proves the mechanism is actually engaging, not just
   silently succeeding by luck.
5. Window/`cwnd` visibly shrinks after a simulated timeout and grows again
   afterward (log it, or add a `--verbose-rdt` flag that prints `cwnd` each
   round) — gives you a concrete artifact to show in the Oral Viva.
6. Throughput comparison: measure transfer time for the same file at a
   couple of different fixed window sizes, to have real numbers ready for
   "defends bandwidth optimisation strategies with mathematical precision"
   (top rubric tier).

---

## 11. Report Mapping (Excellent-Level portion)

| Report section | Comes from |
|---|---|
| 2. Data Structures | `rdt_packet.HEADER_FORMAT` byte-by-byte (§2) — chart every field to bit/byte level for the top documentation tier |
| 3. Flowcharts | Sender state machine (§4), receiver state machine (§5) |
| 6. GenAI Log | Any AI-assisted RDT/CRC code, with your manual review of correctness (e.g. checking checksum placement, timer edge cases) |
| 7. Demo Evidence | HASH match screenshot (§7), retransmit-count log under induced loss (§8, §10) |

---

## 12. Common Pitfalls at This Stage

- **Checksum computed over the wrong bytes**: always zero the checksum
  field before computing CRC32, both when packing and when verifying, or
  every packet will appear "corrupted."
- **Blocking `recvfrom()` with no timeout**: without `selectors`/`select`
  or `sock.settimeout()`, the sender will hang forever if a packet is lost
  — this defeats the entire point of the timer-based retransmit.
- **Datagram size vs MSS**: keep `MSS` well under ~1400 bytes once you add
  the 16-byte header, to avoid IP fragmentation, which silently breaks
  your reliability assumptions.
- **Window that never shrinks**: if `cwnd` only grows and never backs off
  on timeout, you don't actually have congestion control — just a big fixed
  window, which won't satisfy the "Congestion/Flow Control" criterion in
  the Oral Viva if you're asked to explain it.
- **Testing only on localhost with 0% loss**: always run the fault-injected
  tests (Section 9) before the real demo — localhost loopback rarely drops
  packets naturally, so a bug in the retransmit path can hide until the
  actual live demo in front of examiners.
