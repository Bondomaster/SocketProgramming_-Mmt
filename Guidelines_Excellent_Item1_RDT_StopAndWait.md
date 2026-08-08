# Guidelines — Excellent Level, Mục 1: Custom Reliable UDP Layer (RDT)

**Phạm vi:** CHỈ làm mục đầu tiên của Excellent Level —
*"ACKs, sequence numbers, timeout/retransmit"*. **Không** làm Sliding
Window/Congestion Control (mục 2) hay `HASH` (mục 3) trong guideline này.

**Thuật toán chọn: Stop-and-Wait** — vì nó tự thân không cần window (mỗi
lúc chỉ có đúng 1 gói "đang bay"), nên tách biệt hoàn toàn khỏi mục
Sliding Window. Đây là lựa chọn an toàn nhất nếu chỉ muốn dừng ở mục 1:
đơn giản để code đúng, dễ giải thích trong Oral Viva, và không kéo theo
các lỗi tinh vi về window/cwnd như Selective Repeat.

> Nếu sau này muốn làm thêm mục 2 (Sliding Window), chỉ cần thay đổi
> phần "chờ ACK" từ "1 gói tại 1 thời điểm" thành "N gói trong cửa sổ" —
> phần header, checksum, cơ chế ACK/retransmit ở dưới vẫn tái sử dụng
> được nguyên vẹn.

---

## 1. Header gói tin (giữ nguyên, không đổi)

Header 16 byte dùng chung cho cả 2 chiều gửi (đã có sẵn nếu bạn dùng lại
`rdt_packet.py` từ trước — không cần sửa gì ở phần này):

```
Offset (byte)   0        4        8       10       12       16
                ├────────┼────────┼────────┼────────┼────────┤
Field           │Seq Num │Ack Num │ Flags  │ Length │Checksum│  Payload...
Size (byte)     │   4    │   4    │   2    │   2    │   4    │  (≤1024B)
```

```python
# common/rdt_packet.py
import struct
import zlib

HEADER_FORMAT = "!IIHHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)   # 16 byte

FLAG_ACK = 1 << 0
FLAG_FIN = 1 << 2

def pack_packet(seq_num, ack_num, flags, payload: bytes) -> bytes:
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), 0)
    checksum = zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), checksum)
    return header + payload

def unpack_packet(data: bytes):
    seq_num, ack_num, flags, length, checksum = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:HEADER_SIZE + length]
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, length, 0)
    ok = (zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF) == checksum
    return seq_num, ack_num, flags, payload, ok
```

Với Stop-and-Wait, `seq_num` chỉ cần luân phiên **0 và 1** (2 giá trị là
đủ — đây là điểm khác biệt lớn nhất so với Selective Repeat, vốn cần số
thứ tự tăng dần cho cả cửa sổ nhiều gói). Dùng số tăng dần đầy đủ (0, 1,
2, 3...) cũng không sai, chỉ là không bắt buộc — chọn cách nào bạn thấy
dễ giải thích hơn trong Oral Viva.

---

## 2. Sender — Stop-and-Wait

Ý tưởng: gửi 1 gói → chờ đúng ACK của gói đó → nếu timeout thì gửi lại
**y hệt gói đó** → chỉ khi nhận đúng ACK mới chuyển sang gói kế tiếp.

```python
# common/rdt_sender.py
import socket
import time
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN

MSS = 1024
RTO = 1.0            # timeout cố định (giây) — đơn giản, không cần RTO động ở mục 1
MAX_RETRIES = 10      # giới hạn số lần thử lại — QUAN TRỌNG, tránh treo vô hạn

def send_file(sock: socket.socket, dest_addr, chunks: list[bytes]) -> int:
    """Gửi lần lượt từng chunk theo Stop-and-Wait. Trả về số lần đã retransmit."""
    seq = 0
    retransmits = 0
    sock.settimeout(RTO)

    for chunk in chunks:
        pkt = pack_packet(seq, 0, 0, chunk)
        attempts = 0
        acked = False

        while not acked:
            sock.sendto(pkt, dest_addr)
            try:
                data, _ = sock.recvfrom(2048)
                _, ack_num, flags, _, ok = unpack_packet(data)
                if ok and (flags & FLAG_ACK) and ack_num == seq:
                    acked = True          # đúng ACK mong đợi -> chuyển gói kế tiếp
                # nếu ACK sai/hỏng -> vòng while lặp lại, gửi lại gói hiện tại
            except socket.timeout:
                attempts += 1
                retransmits += 1
                if attempts > MAX_RETRIES:
                    raise ConnectionError(
                        f"Không nhận được ACK cho seq={seq} sau {MAX_RETRIES} lần thử — huỷ transfer"
                    )
                # timeout -> vòng while lặp lại, gửi lại gói hiện tại

        seq = 1 - seq   # đảo bit 0/1 cho Stop-and-Wait (hoặc seq += 1 nếu dùng số tăng dần)

    # Gửi gói FIN — CŨNG phải chờ ACK, không gửi 1 lần rồi bỏ mặc (tránh bug
    # "FIN mất gói thì không ai biết transfer đã xong chưa")
    fin_pkt = pack_packet(seq, 0, FLAG_FIN, b"")
    attempts = 0
    acked = False
    while not acked:
        sock.sendto(fin_pkt, dest_addr)
        try:
            data, _ = sock.recvfrom(2048)
            _, ack_num, flags, _, ok = unpack_packet(data)
            if ok and (flags & FLAG_ACK) and ack_num == seq:
                acked = True
        except socket.timeout:
            attempts += 1
            retransmits += 1
            if attempts > MAX_RETRIES:
                raise ConnectionError("Không nhận được ACK cho gói FIN — huỷ transfer")

    return retransmits
```

**Điểm mấu chốt so với thiết kế Selective Repeat trước đây (đã từng gây
deadlock trong bản của nhóm):**
- Có `MAX_RETRIES` rõ ràng — vượt quá thì `raise` lỗi tường minh, **không
  bao giờ lặp vô hạn**.
- Gói `FIN` cũng phải qua đúng cơ chế chờ-ACK-hoặc-hết-hạn như gói dữ
  liệu thường — không gửi 1 lần rồi mặc kệ.

---

## 3. Receiver — Stop-and-Wait

Ý tưởng: nhận gói → kiểm tra checksum → nếu đúng seq đang chờ thì ghi file
và gửi ACK; nếu là **gói trùng** (seq của lần trước, do sender gửi lại vì
ACK bị mất) thì **gửi lại đúng ACK đó nhưng không ghi file lần 2**.

```python
# common/rdt_receiver.py
import socket
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN

IDLE_TIMEOUT = 30.0   # timeout tổng thể cho cả phiên nhận — CHỈ dùng để phát hiện
                      # kết nối chết thật sự, KHÔNG dùng làm điều kiện "đã nhận đủ"

def recv_file(sock: socket.socket, out_path) -> bool:
    """Trả về True nếu nhận trọn vẹn (thấy FIN hợp lệ), False nếu timeout/lỗi."""
    expected_seq = 0
    peer_addr = None
    sock.settimeout(IDLE_TIMEOUT)

    with open(out_path, "wb") as out:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                # Thật sự không còn hoạt động gì trong 30s -> coi là lỗi, KHÔNG
                # coi là "đã xong". Đây là điểm sửa quan trọng so với bug cũ.
                return False

            peer_addr = peer_addr or addr
            seq, _, flags, payload, ok = unpack_packet(data)
            if not ok:
                continue   # gói hỏng -> bỏ qua, không ACK, để sender tự timeout & gửi lại

            if flags & FLAG_FIN:
                ack = pack_packet(0, seq, FLAG_ACK, b"")
                sock.sendto(ack, peer_addr)
                return True   # kết thúc THÀNH CÔNG — có FIN hợp lệ, không phải do timeout

            if seq == expected_seq:
                out.write(payload)
                expected_seq = 1 - expected_seq   # đảo bit, khớp với sender
            # Nếu seq != expected_seq -> đây là gói trùng (do ACK trước bị mất),
            # KHÔNG ghi file lần 2, nhưng VẪN phải ACK lại để sender biết mà dừng gửi lại

            ack = pack_packet(0, seq, FLAG_ACK, b"")
            sock.sendto(ack, peer_addr)
```

**Điểm mấu chốt (sửa đúng lỗi đã gặp trước đây):**
- `IDLE_TIMEOUT` chỉ dùng để phát hiện **kết nối chết thật sự** (30 giây
  không có bất kỳ gói nào, kể cả gói lặp) — không dùng làm dấu hiệu "chắc
  là xong rồi". Việc "xong" chỉ được xác nhận khi **thấy đúng gói FIN**.
- Khi `seq != expected_seq` (gói trùng do sender gửi lại vì ACK trước bị
  mất trên đường về) — **vẫn phải ACK lại**, nếu không sender sẽ tiếp tục
  chờ và gửi lại mãi dù receiver đã nhận đúng từ lần đầu.
- Hàm trả về `True`/`False` rõ ràng — nơi gọi (`STOR` handler) phải kiểm
  tra giá trị này trước khi trả `226`, không được mặc định luôn thành
  công.

---

## 4. Wiring vào `STOR`/`RETR` (chỉ phần khác so với bản chưa có RDT)

```python
@command("STOR")
def cmd_stor(session, args):
    target = resolve_path(session, args)
    if target is None:
        return 550, "Invalid path"
    data_sock = get_session_data_socket(session)   # socket UDP riêng theo session
    success = recv_file(data_sock, target)
    if not success:
        target.unlink(missing_ok=True)   # xoá file dở dang, tránh để lại file rác/sai
        return 426, "Connection closed; transfer aborted"
    return 226, "Transfer complete"

@command("RETR")
def cmd_retr(session, args):
    target = resolve_path(session, args)
    if target is None or not target.is_file():
        return 550, "File unavailable"
    chunks = list(read_chunks(target, chunk_size=MSS))
    data_sock = get_session_data_socket(session)
    dest = get_session_data_peer(session)
    try:
        retransmits = send_file(data_sock, dest, chunks)
    except ConnectionError:
        return 426, "Connection closed; transfer aborted"
    log.info("RETR %s: %d chunks, %d retransmits", args, len(chunks), retransmits)
    return 226, "Transfer complete"
```

---

## 5. Test riêng cho mục 1 (không cần test window/cwnd/HASH)

```python
# tests/test_rdt_stopwait.py
import socket, threading
from common.rdt_sender import send_file
from common.rdt_receiver import recv_file

def test_lossless(tmp_path):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r.bind(("127.0.0.1", 0))
    dest = r.getsockname()
    chunks = [b"x" * 1024 for _ in range(10)]
    out = tmp_path / "out.bin"

    t = threading.Thread(target=lambda: recv_file(r, out), daemon=True)
    t.start()
    send_file(s, dest, chunks)
    t.join(timeout=10)

    assert out.read_bytes() == b"".join(chunks)
```

Để kiểm tra khả năng phục hồi khi mất gói, dùng cách đơn giản: chèn một
hàm `sendto` giả lập rớt gói theo xác suất (chỉ áp dụng khi test, không
đụng vào code sản phẩm — xem `Guidelines_Excellent.md` mục 8/9 nếu muốn
làm phần này kỹ hơn, dù không bắt buộc cho mục 1).

---

## 6. Checklist hoàn thành đúng "Mục 1"

- [ ] Header 16 byte đóng/mở gói đúng, checksum CRC32 phát hiện được lỗi.
- [ ] Sender chờ đúng ACK trước khi gửi gói kế tiếp (Stop-and-Wait thật
      sự — không gửi nhiều gói cùng lúc).
- [ ] Sender timeout + gửi lại đúng gói cũ khi không có ACK.
- [ ] Sender có `MAX_RETRIES` — không lặp vô hạn.
- [ ] Receiver phân biệt được gói mới vs gói trùng (dựa vào `seq`), không
      ghi trùng dữ liệu vào file.
- [ ] Receiver luôn ACK lại kể cả với gói trùng.
- [ ] `FIN` được xác nhận qua ACK, không phải "gửi 1 lần rồi thôi".
- [ ] `recv_file`/`send_file` trả về trạng thái rõ ràng để `STOR`/`RETR`
      biết transfer thật sự thành công hay thất bại.
- [ ] Test với file nhỏ (vài KB) qua loopback, xác nhận byte-for-byte
      giống hệt file gốc.

**Không cần cho mục 1** (để dành cho mục 2/3 nếu sau này làm thêm):
cwnd, sliding window, RTO động (EWMA), `HASH`/SHA-256.
