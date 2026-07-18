# GitHub Issues — Hybrid FTP Project

Danh sách issue đầy đủ, chia theo **Epic** (nhóm chức năng) và **Milestone**
(Basic / Advanced / Excellent — khớp 3 mức đánh giá của đề bài). Copy từng
issue vào GitHub, hoặc dùng script `gh` ở cuối file để tạo tự động bằng
GitHub CLI.

Quy ước nhãn:
- Module: `tcp-control`, `udp-data`, `server-core`, `client-core`, `docs`, `testing`, `chore`
- Cấp độ: `basic-level`, `advanced-level`, `excellent-level`
- Loại: `bug`, `enhancement` (tuỳ chọn, thêm khi cần)

---

## Milestone: `M1 - Basic Level`

### Epic: Kênh điều khiển TCP
- [ ] **Khởi tạo kết nối TCP Client–Server bằng native socket API**
  `tcp-control` `basic-level`
- [ ] **Thiết kế wire format lệnh/phản hồi (`<CMD> <ARGS>\r\n`, parser dòng lệnh)**
  `tcp-control` `basic-level`
- [ ] **Cài đặt xác thực người dùng (USER, PASS) + state machine đăng nhập**
  `tcp-control` `basic-level`
- [ ] **Thiết lập bảng mã phản hồi 3 chữ số (1xx–5xx) và hàm format_reply()**
  `tcp-control` `basic-level`
- [ ] **Cài đặt QUIT, NOOP, PWD**
  `tcp-control` `basic-level`
- [ ] **Cài đặt nhóm lệnh thông tin: STAT, SIZE, MDTM, HELP**
  `tcp-control` `basic-level`

### Epic: Kênh dữ liệu UDP & RDT
- [ ] **Thiết lập UDP socket độc lập cho data channel (bind ephemeral port)**
  `udp-data` `basic-level`
- [ ] **Truyền/nhận file ASCII cơ bản qua UDP (chưa cần reliable)**
  `udp-data` `basic-level`
- [ ] **Cài đặt TYPE {A|I} — phân biệt chế độ ASCII / Binary**
  `tcp-control` `udp-data` `basic-level`

### Epic: Kiến trúc Server & Client
- [ ] **Server single-thread, xử lý 1 client tại 1 thời điểm (baseline trước khi làm đa luồng)**
  `server-core` `basic-level`
- [ ] **Client CLI tối thiểu: connect, login, get/put 1 file, quit**
  `client-core` `basic-level`
- [ ] **Xây dựng CLI hiển thị trạng thái kết nối và tiến độ transfer**
  `client-core` `server-core` `basic-level`

### Epic: Tài liệu & Đánh giá
- [ ] **Khởi tạo repo, CMakeLists.txt, cấu trúc thư mục, .gitignore**
  `chore` `basic-level`
- [ ] **Lập Task Assignment Matrix (module owner + collaborators)**
  `docs` `basic-level`
- [ ] **Viết Self-Assessment & Peer Evaluation template (%đóng góp)**
  `docs` `basic-level`
- [ ] **Viết GenAI Usage & Refinement Log (prompt, raw output, refinement)**
  `docs` `basic-level`

---

## Milestone: `M2 - Advanced Level`

### Epic: Kênh điều khiển TCP
- [ ] **Triển khai bộ lệnh điều hướng thư mục: CWD, CDUP, MKD, RMD, LIST, NLST**
  `tcp-control` `advanced-level`
- [ ] **Cài đặt nhóm lệnh quản lý file: DELE, RNFR/RNTO, APPE, STOU**
  `tcp-control` `advanced-level`
- [ ] **Cài đặt TYPE/MODE đầy đủ + ABOR (hủy transfer giữa chừng, reset data channel)**
  `tcp-control` `advanced-level`
- [ ] **Chống path traversal — validate mọi path nằm trong sandbox root của session**
  `tcp-control` `advanced-level`

### Epic: Kênh dữ liệu UDP & RDT
- [ ] **Thiết lập truyền tải binary an toàn (ảnh, video, archive) không hỏng dữ liệu**
  `udp-data` `advanced-level`
- [ ] **Xử lý logic chuyển đổi Active Mode (PORT) và Passive Mode (PASV)**
  `server-core` `udp-data` `advanced-level`

### Epic: Kiến trúc Server
- [ ] **Lập trình cơ chế đa luồng (multi-thread per session) cho Server**
  `server-core` `advanced-level`
- [ ] **Bảo vệ shared state (session table) bằng mutex, tránh race condition**
  `server-core` `advanced-level`
- [ ] **Logging chi tiết: connect/disconnect, lệnh thực thi, tiến độ transfer, session table**
  `server-core` `docs` `advanced-level`

### Epic: Kiến trúc Client
- [ ] **Client hỗ trợ duyệt cây thư mục nested (đồng bộ với LIST/CWD server)**
  `client-core` `advanced-level`
- [ ] **Client chạy transfer trên thread riêng để CLI không bị block**
  `client-core` `advanced-level`

### Epic: Tài liệu & Đánh giá
- [ ] **Viết báo cáo kỹ thuật: sequence diagram toàn bộ vòng đời TCP+UDP**
  `docs` `advanced-level`
- [ ] **Viết báo cáo: cấu trúc dữ liệu (RdtHeader, Session struct, dispatch table)**
  `docs` `advanced-level`
- [ ] **Viết báo cáo: flowchart server thread-dispatch + Active/Passive toggle**
  `docs` `advanced-level`
- [ ] **Test 2+ client đồng thời, xác nhận session isolation (không đụng file/path)**
  `testing` `advanced-level`
- [ ] **Test ASCII/Binary round-trip byte-for-byte (diff/sha256sum)**
  `testing` `advanced-level`

---

## Milestone: `M3 - Excellent Level`

### Epic: Kênh dữ liệu UDP & RDT (trọng tâm — chia 2 người: sender / receiver)
- [ ] **Định nghĩa custom UDP header (seq_num, ack_num, flags, length, checksum)**
  `udp-data` `excellent-level`
- [ ] **Cài checksum (CRC32) — phát hiện gói tin lỗi/hỏng**
  `udp-data` `excellent-level`
- [ ] **Chọn & cài đặt thuật toán RDT: Selective Repeat (khuyến nghị) hoặc Go-Back-N**
  `udp-data` `excellent-level`
- [ ] **Sender state machine: sliding window, per-packet timer, resend on timeout**
  `udp-data` `excellent-level`
- [ ] **Receiver state machine: buffer out-of-order, loại trùng lặp (dedup theo seq), ghi file theo đúng thứ tự**
  `udp-data` `excellent-level`
- [ ] **RTO động (EWMA theo Jacobson/Karels) thay cho fixed timeout**
  `udp-data` `excellent-level`
- [ ] **Áp dụng cơ chế Sliding Window / cwnd kiểu slow-start + giảm nửa khi timeout (congestion control)**
  `udp-data` `excellent-level`

### Epic: Kiến trúc Server/Client
- [ ] **Cài đặt lệnh HASH (MD5/SHA-256) xác minh toàn vẹn dữ liệu trước/sau transfer**
  `server-core` `client-core` `excellent-level`
- [ ] **Client: workflow HASH tự động so sánh trước/sau, hiển thị match/mismatch trên CLI**
  `client-core` `excellent-level`

### Epic: Testing
- [ ] **Viết fault-injection harness: --drop-rate, --corrupt-rate để demo phục hồi lỗi**
  `testing` `excellent-level`
- [ ] **Test phục hồi khi mất gói / hỏng gói — xác nhận zero data loss**
  `testing` `excellent-level`
- [ ] **Đo throughput/hiệu năng khi thay đổi window size — số liệu cho phần bảo vệ oral viva**
  `testing` `excellent-level`

### Epic: Tài liệu & Đánh giá
- [ ] **Viết báo cáo: bảng phân tích từng byte header UDP (bit/byte-level)**
  `docs` `excellent-level`
- [ ] **Chuẩn bị kịch bản demo: upload/download, so sánh hash, connected-client table, concurrent test**
  `docs` `testing` `excellent-level`
- [ ] **Chuẩn bị luận điểm bảo vệ Oral Viva: giải thích RDT state, đánh đổi bandwidth/reliability**
  `docs` `excellent-level`

---

## Script tạo issue tự động bằng GitHub CLI (`gh`)

Nếu muốn tạo nhanh toàn bộ issue trên (không cần copy tay), lưu file này
thành `create_issues.sh`, chỉnh `REPO`, rồi chạy:

```bash
#!/usr/bin/env bash
REPO="your-org/hybrid-ftp"   # đổi thành repo thật

create_issue () {
  gh issue create --repo "$REPO" --title "$1" --label "$2" --milestone "$3"
}

# Milestone: M1 - Basic Level
create_issue "Khởi tạo kết nối TCP Client–Server bằng native socket API" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Thiết kế wire format lệnh/phản hồi" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Cài đặt xác thực người dùng (USER, PASS)" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Thiết lập bảng mã phản hồi 3 chữ số" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Cài đặt QUIT, NOOP, PWD" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Cài đặt nhóm lệnh thông tin: STAT, SIZE, MDTM, HELP" "tcp-control,basic-level" "M1 - Basic Level"
create_issue "Thiết lập UDP socket độc lập cho data channel" "udp-data,basic-level" "M1 - Basic Level"
create_issue "Truyền/nhận file ASCII cơ bản qua UDP" "udp-data,basic-level" "M1 - Basic Level"
create_issue "Cài đặt TYPE {A|I}" "tcp-control,udp-data,basic-level" "M1 - Basic Level"
create_issue "Server single-thread baseline" "server-core,basic-level" "M1 - Basic Level"
create_issue "Client CLI tối thiểu" "client-core,basic-level" "M1 - Basic Level"
create_issue "CLI hiển thị trạng thái kết nối/tiến độ" "client-core,server-core,basic-level" "M1 - Basic Level"
create_issue "Khởi tạo repo, CMake, .gitignore" "chore,basic-level" "M1 - Basic Level"
create_issue "Task Assignment Matrix" "docs,basic-level" "M1 - Basic Level"
create_issue "Self-Assessment & Peer Evaluation template" "docs,basic-level" "M1 - Basic Level"
create_issue "GenAI Usage & Refinement Log" "docs,basic-level" "M1 - Basic Level"

# Milestone: M2 - Advanced Level
create_issue "Bộ lệnh điều hướng thư mục: CWD, CDUP, MKD, RMD, LIST, NLST" "tcp-control,advanced-level" "M2 - Advanced Level"
create_issue "Nhóm lệnh quản lý file: DELE, RNFR/RNTO, APPE, STOU" "tcp-control,advanced-level" "M2 - Advanced Level"
create_issue "TYPE/MODE đầy đủ + ABOR" "tcp-control,advanced-level" "M2 - Advanced Level"
create_issue "Chống path traversal" "tcp-control,advanced-level" "M2 - Advanced Level"
create_issue "Truyền tải binary an toàn" "udp-data,advanced-level" "M2 - Advanced Level"
create_issue "Active Mode (PORT) / Passive Mode (PASV)" "server-core,udp-data,advanced-level" "M2 - Advanced Level"
create_issue "Đa luồng (multi-thread per session)" "server-core,advanced-level" "M2 - Advanced Level"
create_issue "Bảo vệ shared state bằng mutex" "server-core,advanced-level" "M2 - Advanced Level"
create_issue "Logging chi tiết + session table" "server-core,docs,advanced-level" "M2 - Advanced Level"
create_issue "Client duyệt cây thư mục nested" "client-core,advanced-level" "M2 - Advanced Level"
create_issue "Client transfer trên thread riêng" "client-core,advanced-level" "M2 - Advanced Level"
create_issue "Báo cáo: sequence diagram TCP+UDP" "docs,advanced-level" "M2 - Advanced Level"
create_issue "Báo cáo: cấu trúc dữ liệu" "docs,advanced-level" "M2 - Advanced Level"
create_issue "Báo cáo: flowchart server + Active/Passive" "docs,advanced-level" "M2 - Advanced Level"
create_issue "Test 2+ client đồng thời" "testing,advanced-level" "M2 - Advanced Level"
create_issue "Test ASCII/Binary round-trip" "testing,advanced-level" "M2 - Advanced Level"

# Milestone: M3 - Excellent Level
create_issue "Custom UDP header (seq/ack/flags/checksum)" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Checksum CRC32" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Thuật toán RDT: Selective Repeat" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Sender state machine: sliding window + timer" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Receiver state machine: dedup + reorder" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "RTO động (EWMA)" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Sliding Window / cwnd congestion control" "udp-data,excellent-level" "M3 - Excellent Level"
create_issue "Lệnh HASH (MD5/SHA-256)" "server-core,client-core,excellent-level" "M3 - Excellent Level"
create_issue "Client workflow HASH tự động so sánh" "client-core,excellent-level" "M3 - Excellent Level"
create_issue "Fault-injection harness (--drop-rate/--corrupt-rate)" "testing,excellent-level" "M3 - Excellent Level"
create_issue "Test phục hồi mất gói/hỏng gói" "testing,excellent-level" "M3 - Excellent Level"
create_issue "Đo throughput theo window size" "testing,excellent-level" "M3 - Excellent Level"
create_issue "Báo cáo: bảng bit/byte header UDP" "docs,excellent-level" "M3 - Excellent Level"
create_issue "Kịch bản demo đầy đủ" "docs,testing,excellent-level" "M3 - Excellent Level"
create_issue "Luận điểm bảo vệ Oral Viva" "docs,excellent-level" "M3 - Excellent Level"

echo "Đã tạo xong tất cả issue."
```

> Yêu cầu: đã cài `gh` CLI và đăng nhập (`gh auth login`), đã tạo trước 3
> milestone (`M1 - Basic Level`, `M2 - Advanced Level`, `M3 - Excellent
> Level`) và các nhãn tương ứng trên repo (`gh label create ...`) trước khi
> chạy script — GitHub CLI không tự tạo milestone/label nếu chưa tồn tại.
