# Guidelines — Hybrid FTP Application (C++)

Implementation guide for the *Design and Implementation of the Hybrid FTP* lab
project, using **C++ with POSIX/BSD sockets** (Linux/macOS — use Winsock
equivalents if targeting Windows).

Architecture recap: **TCP control channel** (commands, replies) + **UDP data
channel** (file payload) with a **custom reliability layer built on raw UDP**.

---

## 1. Project Layout

```
hybrid-ftp/
├── CMakeLists.txt
├── common/
│   ├── protocol.h          # Command strings, reply codes, constants
│   ├── udp_packet.h        # Custom UDP header struct + (de)serialization
│   ├── checksum.h          # CRC32 / simple checksum
│   └── hash.h              # SHA-256 wrapper (integrity check)
├── server/
│   ├── main.cpp
│   ├── session.h/.cpp      # Per-client session state + thread entry point
│   ├── command_handler.cpp # Dispatch table for FTP commands
│   ├── rdt_receiver.cpp    # Reliable UDP receive logic (uploads)
│   └── rdt_sender.cpp      # Reliable UDP send logic (downloads)
├── client/
│   ├── main.cpp
│   ├── cli.cpp             # Command-line front end
│   ├── rdt_sender.cpp
│   └── rdt_receiver.cpp
└── tests/
    └── ... (packet loss / corruption simulation harness)
```

Keep `rdt_sender`/`rdt_receiver` shared between client and server as a small
static library (`librdt.a`) — the reliability logic is identical on both
sides, only the direction of data flow differs.

---

## 2. Control Channel (TCP) Design

### 2.1 Socket setup

```cpp
// Server
int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
int opt = 1;
setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
bind(listen_fd, (sockaddr*)&addr, sizeof(addr));
listen(listen_fd, /*backlog=*/16);

while (true) {
    sockaddr_in client_addr{};
    socklen_t len = sizeof(client_addr);
    int client_fd = accept(listen_fd, (sockaddr*)&client_addr, &len);
    std::thread(session_worker, client_fd, client_addr).detach();
}
```

Each accepted connection gets its own thread (or task on a thread pool) so
that sessions are fully isolated — this satisfies the Advanced-level
"Concurrency Control" requirement. Guard any shared server-wide state
(e.g., the connected-client table) with a `std::mutex`.

### 2.2 Wire format for commands and replies

Use plain text lines terminated by `\r\n`, matching real FTP:

```
Client → Server:  USER alice\r\n
Server → Client:  331 Username OK, need password\r\n
Client → Server:  PASS secret\r\n
Server → Client:  230 Login successful\r\n
```

Parsing rule: read until `\r\n`, split on the first space into
`<COMMAND> <ARGS>`. Reply lines always start with the 3-digit code from
Section 2.3 of the spec, followed by a space and human-readable text.

```cpp
struct Reply { int code; std::string text; };

std::string format_reply(const Reply& r) {
    return std::to_string(r.code) + " " + r.text + "\r\n";
}
```

### 2.3 Command dispatch table

Map every command from the approved list (`USER, PASS, QUIT, NOOP, PWD, CWD,
CDUP, MKD, RMD, LIST, NLST, STAT, SIZE, MDTM, TYPE, MODE, PORT, PASV, RETR,
STOR, STOU, APPE, DELE, RNFR, RNTO, HASH, ABOR, HELP`) to a handler function
taking `(Session&, const std::string& args) -> Reply`. A `std::unordered_map<
std::string, Handler>` built once at startup keeps `command_handler.cpp`
flat and easy to extend.

Session state to track per client:

```cpp
struct Session {
    int ctrl_fd;
    std::string username;
    bool authenticated = false;
    std::filesystem::path cwd;
    char type = 'A';           // 'A' ASCII or 'I' Image/Binary
    char mode = 'S';           // Stream / Block / Compressed
    enum { NONE, ACTIVE, PASSIVE } data_mode = NONE;
    sockaddr_in data_peer{};   // for PORT (active)
    int pasv_listen_fd = -1;   // for PASV (passive)
    std::string rename_from;   // set by RNFR, consumed by RNTO
};
```

### 2.4 Authentication and state machine notes

- `USER` before `PASS` → reply `331`; `PASS` without prior `USER` → `503`-style
  error (or `530`).
- Commands that require login (`LIST`, `RETR`, `STOR`, …) must check
  `authenticated` and return `530 Not logged in` otherwise.
- `RNFR` must set `rename_from` and reply `350`; a following `RNTO` performs
  the rename and clears the field; `RNTO` without a preceding `RNFR` is a
  `503`/`503`-style sequencing error.
- Validate all paths against the session's root/chroot directory to prevent
  path traversal (`../../etc/passwd`) — this is worth mentioning in the
  report as a security consideration.

---

## 3. Data Channel (UDP) Design

### 3.1 Active vs Passive mode

| | Active (`PORT`) | Passive (`PASV`) |
|---|---|---|
| Who opens the data socket | Client opens it, tells server via `PORT h1,h2,h3,h4,p1,p2` | Server opens it, tells client the port in its `227` reply |
| Who initiates the transfer | Server sends first datagram to client's address | Client sends first datagram to server's address |
| Firewall/NAT friendliness | Poor — inbound connection to client | Good — client-only outbound |

Implement both; default to Passive for Advanced level, but Active must still
work for the command coverage requirement.

```cpp
// PASV handler (server)
int data_fd = socket(AF_INET, SOCK_DGRAM, 0);
sockaddr_in bind_addr{AF_INET, htons(0), INADDR_ANY}; // ephemeral port
bind(data_fd, (sockaddr*)&bind_addr, sizeof(bind_addr));
socklen_t len = sizeof(bind_addr);
getsockname(data_fd, (sockaddr*)&bind_addr, &len);
uint16_t port = ntohs(bind_addr.sin_port);
// reply 227 with server IP + (port>>8, port&0xFF)
```

### 3.2 Custom UDP header (RDT layer)

Since raw UDP is unreliable and its own 8-byte header is not accessible from
user space, define an **application-layer header** placed at the front of
every UDP datagram payload:

```cpp
#pragma pack(push, 1)
struct RdtHeader {
    uint32_t seq_num;      // sequence number of this segment
    uint32_t ack_num;      // cumulative ACK (0 if this is a DATA packet)
    uint16_t flags;        // bit0=ACK, bit1=SYN, bit2=FIN, bit3=EOF, bit4=NAK
    uint16_t length;       // payload length in bytes (excludes header)
    uint32_t checksum;     // CRC32 over header(checksum=0)+payload
};
#pragma pack(pop)
// Total header size: 16 bytes
```

Flags:
- `SYN` — first packet of a transfer, carries filename/size metadata.
- `EOF`/`FIN` — final data packet, transfer complete.
- `ACK` — this datagram is an acknowledgment, `ack_num` = next expected seq.
- `NAK` (optional) — explicit negative ack for corrupted/duplicate packet.

Checksum: compute CRC32 (or even a simple 16-bit sum) over the header (with
the checksum field zeroed) plus payload before sending; recompute and
compare on receipt to detect corruption.

### 3.3 Choosing an RDT algorithm

Pick **one** and implement it well rather than half-implementing several:

| Algorithm | Complexity | Fits which level |
|---|---|---|
| Stop-and-Wait | Low | Excellent (baseline) |
| Go-Back-N | Medium | Excellent (better throughput) |
| Selective Repeat | High | Excellent (best throughput, most defensible in viva) |

**Recommendation:** implement **Selective Repeat with a sliding window** —
it directly satisfies "Congestion / Flow Control: Sliding Window" and gives
you the most to discuss in the Oral Viva (window size, buffer management,
per-packet timers).

### 3.4 Sender state machine (pseudocode)

```cpp
void rdt_send_file(UdpSocket& sock, sockaddr_in dest, const fs::path& file) {
    auto chunks = split_into_chunks(file, MSS);           // e.g. 1024B payload
    uint32_t base = 0, next_seq = 0;
    const uint32_t window = SLIDING_WINDOW_SIZE;          // e.g. 8
    std::map<uint32_t, Timer> timers;

    while (base < chunks.size()) {
        // 1. Send everything within the window not yet sent
        while (next_seq < chunks.size() && next_seq < base + window) {
            send_packet(sock, dest, make_data_packet(next_seq, chunks[next_seq]));
            timers[next_seq] = start_timer(RTO_MS);
            next_seq++;
        }
        // 2. Wait for next ACK or timeout (select()/poll() with a timeout)
        auto result = wait_for_ack_or_timeout(sock, timers);
        if (result.timed_out) {
            resend(sock, dest, result.expired_seq);         // per-packet timeout (SR)
            restart_timer(timers, result.expired_seq);
        } else if (valid_ack(result.packet)) {
            timers.erase(result.packet.ack_num);
            acked.insert(result.packet.ack_num);
            while (acked.count(base)) { acked.erase(base); base++; }  // slide window
        }
        // else: corrupted ACK -> checksum mismatch -> ignore, let timer fire
    }
    send_packet(sock, dest, make_fin_packet(next_seq));      // signal completion
}
```

### 3.5 Receiver state machine (pseudocode)

```cpp
void rdt_recv_file(UdpSocket& sock, fs::path out_path) {
    std::map<uint32_t, std::vector<uint8_t>> buffer;   // out-of-order buffer
    uint32_t expected = 0;
    std::ofstream out(out_path, std::ios::binary);

    while (true) {
        auto [pkt, sender] = recv_packet(sock);          // recvfrom()
        if (!checksum_ok(pkt)) { continue; }              // silently drop, sender retransmits on timeout
        if (pkt.has_flag(FIN) && pkt.seq_num == expected) {
            flush_in_order(buffer, out, expected);
            send_ack(sock, sender, expected);
            break;
        }
        if (pkt.seq_num >= expected && pkt.seq_num < expected + RECV_WINDOW) {
            buffer[pkt.seq_num] = pkt.payload;             // buffer / de-dup automatically (map)
        }
        // Always ACK highest in-order seq received so far (cumulative),
        // or per-packet ACK for Selective Repeat (ack each valid seq individually).
        while (buffer.count(expected)) {
            out.write(buffer[expected].data(), buffer[expected].size());
            buffer.erase(expected);
            expected++;
        }
        send_ack(sock, sender, expected);   // duplicate packets simply get re-ACKed, no re-buffering
    }
}
```

Key correctness properties this design gives you, mapped to the spec's
requirements:
- **Zero packet loss** → timers + retransmission.
- **Corruption detection** → checksum field, drop on mismatch.
- **Duplicate elimination** → `std::map`/set keyed by `seq_num` naturally
  ignores re-received duplicates.
- **Correct ordering** → data is only written to disk once contiguous from
  `expected`; out-of-order packets are buffered, not discarded.

### 3.6 Timers and RTO

Use a fixed RTO first (e.g., 300–500 ms) and mention in the report that a
dynamic RTO (Jacobson/Karels EWMA of measured RTT) is the natural next step
for the "Excellent" congestion-control criterion — implement it if time
allows, since it is easy to justify in the Oral Viva.

```cpp
double estimated_rtt = 0, dev_rtt = 0;
void update_rto(double sample_rtt) {
    estimated_rtt = 0.875 * estimated_rtt + 0.125 * sample_rtt;
    dev_rtt        = 0.75  * dev_rtt + 0.25 * std::abs(sample_rtt - estimated_rtt);
    rto_ms = estimated_rtt + 4 * dev_rtt;
}
```

### 3.7 Flow / congestion control

A simple, defensible approach: treat `window` (Section 3.4) as a
`cwnd`-like value that:
- starts small (e.g., 2 packets),
- doubles each successful full-window round (slow start),
- halves on a timeout (congestion signal), similar to TCP's Fast Recovery.

This is enough to satisfy "Sliding Window or equivalent mechanism to
prevent network flooding" without reimplementing all of TCP Reno.

---

## 4. Binary vs ASCII Transfer (`TYPE`)

- `TYPE A` (ASCII): open files in text mode; be consistent about line-ending
  handling if cross-platform testing is required (optional — many
  implementations treat ASCII and binary identically on Linux, since `\n`
  handling differences mostly matter for Windows interop; document whichever
  choice you make).
- `TYPE I` (Image/Binary): open with `std::ios::binary`; **never** run any
  text transformation on the byte stream. This mode is what you'll use for
  images/video/archives (Advanced level requirement).

Always chunk file reads (`MSS`-sized buffers) rather than loading whole
files into memory — this matters for large binary files and shows good
engineering judgment in review.

---

## 5. Directory & File Operations

Implement with `<filesystem>` (C++17):

```cpp
Reply handle_LIST(Session& s, const std::string& args) {
    fs::path target = args.empty() ? s.cwd : s.cwd / args;
    std::ostringstream out;
    for (auto& entry : fs::directory_iterator(target)) {
        out << format_entry(entry) << "\r\n";   // name, size, type, perms
    }
    send_over_data_channel(s, out.str());        // LIST result goes on data channel per RFC 959
    return {150, "Opening data connection"};      // followed by 226 once sent
}
```

Note: `LIST`/`NLST` results are traditionally sent over the **data channel**,
not the control channel, mirroring real FTP — decide explicitly and document
whichever you choose (sending small listings over TCP control channel
directly is an acceptable simplification if you state it in the report).

`SIZE`, `MDTM`, `DELE`, `RNFR`/`RNTO`, `MKD`, `RMD`, `CWD`, `CDUP`, `PWD` are
all straightforward wrappers around `<filesystem>` calls — validate the
resulting path stays inside the session's sandboxed root before touching
the filesystem.

---

## 6. Data Integrity Verification (`HASH`)

```cpp
#include <openssl/sha.h>   // or a header-only SHA-256 implementation if
                           // external libraries beyond the language runtime
                           // are disallowed by your instructor — check 2.1
std::string sha256_file(const fs::path& p) {
    SHA256_CTX ctx; SHA256_Init(&ctx);
    std::ifstream f(p, std::ios::binary);
    char buf[8192]; std::streamsize n;
    while ((n = f.read(buf, sizeof(buf)).gcount()) > 0) SHA256_Update(&ctx, buf, n);
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256_Final(digest, &ctx);
    return to_hex(digest, SHA256_DIGEST_LENGTH);
}
```

> ⚠️ The spec bans third-party FTP frameworks, not general-purpose crypto
> libraries — but **check with your instructor** whether OpenSSL counts as
> an "external library" for this project. If in doubt, implement SHA-256 or
> MD5 from scratch (well-documented public-domain reference algorithms are
> fine to study and reimplement yourself; document this in the GenAI log if
> AI assistance was used).

Client workflow: `HASH filename` before/after transfer, compare digests
locally, report match/mismatch in the CLI — this is exactly the "Demo
Evidence" screenshot you need for the report (Section 2.4, item 7).

---

## 7. Concurrency Model

- **Server:** one thread per accepted TCP control connection (`std::thread`
  or a thread pool with `std::async`/a task queue). Each session owns its
  own UDP data socket (bound to an ephemeral port) so concurrent clients
  never collide on the data channel.
- **Shared state** (connected-client table for the demo log) protected by a
  `std::mutex`; take a lock only around the map mutation, not around the
  whole session loop, to avoid serializing sessions.
- **Client:** can stay single-threaded (one control connection, one data
  transfer at a time) unless you want a responsive CLI during large
  transfers, in which case run the data transfer on a worker thread and
  join it before sending the next control command.

```cpp
void session_worker(int ctrl_fd, sockaddr_in client_addr) {
    Session s{ctrl_fd, ...};
    {
        std::lock_guard lock(g_clients_mutex);
        g_clients[ctrl_fd] = {client_addr, "connected"};
    }
    send_reply(ctrl_fd, {220, "Service ready"});
    std::string line;
    while (read_line(ctrl_fd, line)) {
        auto [cmd, args] = parse_command(line);
        Reply r = dispatch(cmd, s, args);
        send_reply(ctrl_fd, r);
        if (cmd == "QUIT") break;
    }
    {
        std::lock_guard lock(g_clients_mutex);
        g_clients.erase(ctrl_fd);
    }
    close(ctrl_fd);
}
```

---

## 8. CLI / Logging Requirements

The spec requires the app to "report network states, commands issued, and
transfer progress." Minimum logging on the server:

```
[2026-07-17 10:02:11] CONNECT   127.0.0.1:52344 (session #3)
[2026-07-17 10:02:11] CMD       USER alice
[2026-07-17 10:02:11] CMD       PASS ****
[2026-07-17 10:02:12] CMD       PASV -> 227 (127,0,0,1,200,15)
[2026-07-17 10:02:12] XFER      STOR photo.jpg  [==========] 100% (2.1MB, 4.3s, 0 retransmits)
[2026-07-17 10:02:16] DISCONNECT 127.0.0.1:52344
```

Keep an in-memory "active session table" printable on demand (e.g., typing
`SESSIONS` at the server console) — this is the screenshot you need for the
Demo Evidence section and for proving concurrency in the Oral Viva.

---

## 9. Testing Checklist (map directly to Section 4.5 of the spec)

1. Build cleanly with CMake on a fresh machine/container (no leftover local
   paths, no missing headers).
2. Single ASCII upload + download round-trip, byte-for-byte identical.
3. Single binary upload + download (image or archive), byte-for-byte
   identical — verify with `HASH`/`diff`/`sha256sum`.
4. Two clients connected simultaneously, each doing an independent
   transfer — confirm no cross-talk in server logs or file paths.
5. Simulate packet loss / corruption artificially (see Section 10) and
   confirm the RDT layer recovers without data loss.
6. Active mode and Passive mode both work for at least one transfer each.
7. `ABOR` mid-transfer cleanly resets the data channel without crashing the
   session.
8. Directory operations (`MKD`, `CWD`, `LIST`, `RMD`, rename via
   `RNFR`/`RNTO`, `DELE`) all verified against a real nested directory tree.

---

## 10. Simulating Loss/Corruption for the Demo

Don't rely on real network loss (unreliable to reproduce live in front of
examiners). Add a debug-only hook in the UDP send path:

```cpp
#ifdef RDT_SIMULATE_FAULTS
bool should_drop()    { return (rand() % 100) < DROP_PERCENT; }
bool should_corrupt() { return (rand() % 100) < CORRUPT_PERCENT; }
#endif

void send_packet(UdpSocket& sock, sockaddr_in dest, RdtPacket pkt) {
#ifdef RDT_SIMULATE_FAULTS
    if (should_drop()) return;                 // pretend it never left
    if (should_corrupt()) pkt.payload[0] ^= 0xFF;
#endif
    sock.sendto(pkt.serialize(), dest);
}
```

Toggle via a compile flag or a CLI switch (`--drop-rate 10 --corrupt-rate
5`) so you can demonstrate recovery live and explain exactly what's
happening — this plays directly into the "Live Coding & On-the-Spot
Debugging" rubric criterion.

---

## 11. Mapping Work to the Technical Report (Section 2.4)

| Report section | Where it comes from in this codebase |
|---|---|
| 1. Protocol Interaction (sequence diagram) | Trace one full session: TCP handshake → `USER`/`PASS` → `PASV`/`PORT` → UDP data exchange (SYN → DATA*N → FIN/ACK) → `QUIT` |
| 2. Data Structures | `RdtHeader` (§3.2), `Session` struct (§2.3), command dispatch table |
| 3. Flowcharts | Server thread-dispatch loop (§7), sender/receiver state machines (§3.4–3.5), Active/Passive toggle (§3.1) |
| 4. Task Assignment Matrix | Split by module: control-channel/commands, RDT sender, RDT receiver, CLI/logging, testing harness |
| 5. Peer Evaluation | Each member should be able to defend the specific file(s) they owned in the Oral Viva |
| 6. GenAI Log | Record prompts/output for anything AI-assisted (e.g., "generate CRC32 implementation," "explain Selective Repeat pseudocode") plus the manual review/fixes you made |
| 7. Demo Evidence | Screenshots from §8 (session log), §6 (HASH match), §9 test 4 (concurrent sessions) |

---

## 12. Build Setup (reference `CMakeLists.txt`)

```cmake
cmake_minimum_required(VERSION 3.16)
project(HybridFTP CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(common STATIC common/checksum.cpp common/udp_packet.cpp)
add_executable(ftp_server server/main.cpp server/session.cpp server/command_handler.cpp
                           server/rdt_receiver.cpp server/rdt_sender.cpp)
add_executable(ftp_client client/main.cpp client/cli.cpp
                           client/rdt_sender.cpp client/rdt_receiver.cpp)

target_link_libraries(ftp_server PRIVATE common pthread)
target_link_libraries(ftp_client PRIVATE common pthread)
```

---

## 13. Common Pitfalls

- **Forgetting `htons`/`ntohs`/`htonl`/`ntohl`** on port and multi-byte
  header fields — a frequent source of "works on my machine" bugs across
  different endianness assumptions.
- **UDP datagram size vs MSS**: keep payload chunks well under 1500 bytes
  (account for the 16-byte `RdtHeader` + IP/UDP overhead) to avoid IP
  fragmentation, which defeats your own reliability layer's assumptions.
- **Blocking `recvfrom()` with no timeout** will hang the sender forever if
  a packet is lost — always use `select()`/`poll()` or `SO_RCVTIMEO` so the
  retransmission timer can actually fire.
- **Reusing one UDP socket across multiple concurrent client sessions**
  without per-session `addr` filtering — bind a fresh ephemeral-port socket
  per session (Section 3.1) instead of sharing one global data socket.
- **Not resetting session state on `ABOR`** — leftover buffered packets from
  an aborted transfer can corrupt the *next* transfer if the receive buffer
  isn't cleared.

---

## 14. Suggested Build Order

1. TCP control channel skeleton: `USER`/`PASS`/`QUIT`/`NOOP`/`PWD` only,
   single-threaded, single client. Confirm reply codes look right.
2. Add `PASV`, minimal UDP echo (no reliability yet), confirm `STOR`/`RETR`
   of a small ASCII file works over an unreliable UDP send/recv.
3. Layer in `RdtHeader` + Stop-and-Wait — confirm files transfer correctly
   with simulated loss on (Section 10).
4. Upgrade to Selective Repeat with a sliding window; add RTO estimation.
5. Add binary `TYPE I` support, directory commands, rename, delete.
6. Add multi-threading + session table + logging.
7. Add `HASH`/SHA-256 integrity check end-to-end.
8. Add `ABOR`, `PORT`/Active mode, `APPE`, `STOU`, `HELP`.
9. Polish CLI, write test harness, run the full checklist (Section 9),
   then write the report.

---

*This guide is a technical roadmap, not a spec substitute — always cross-check
against the original project brief and course-specific constraints (e.g.,
whether crypto/hash libraries are considered "external libraries" for your
course) before finalizing design decisions.*
