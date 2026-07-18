# Quy tắc làm việc với Git & GitHub — Hybrid FTP Project

Áp dụng cho nhóm 2–3 người. Mục tiêu: lịch sử commit rõ ràng, đủ để giảng
viên xác minh mức đóng góp từng người (yêu cầu Section 4.4 của đề bài).

---

## 1. Cấu trúc branch

```
main            # code luôn chạy được, chỉ merge qua Pull Request
├── dev         # nhánh tích hợp chung, merge từ các feature branch vào đây
│   ├── feature/tcp-control       # ai làm control channel
│   ├── feature/udp-rdt-sender    # ai làm reliable UDP sender
│   ├── feature/udp-rdt-receiver  # ai làm reliable UDP receiver
│   ├── feature/directory-ops     # LIST/MKD/RMD/RNFR/RNTO...
│   └── feature/cli-logging       # CLI + logging
```

- **Không push trực tiếp lên `main`.**
- Mỗi thành viên làm việc trên branch riêng theo module mình phụ trách
  (khớp với Task Assignment Matrix trong báo cáo).
- Đặt tên branch: `feature/<ten-module>` hoặc `fix/<loi-gi>`.

## 2. Quy tắc commit

- Commit nhỏ, thường xuyên — **không** commit 1 lần cả module to (giảng
  viên có thể yêu cầu xem lịch sử commit để kiểm tra ai làm gì).
- Format message (Conventional Commits):

  ```
  <type>(<module>): <mô tả ngắn>

  type: feat | fix | refactor | docs | test | chore
  ```

  Ví dụ:
  ```
  feat(rdt-sender): implement selective repeat sliding window
  fix(control): correct RNFR/RNTO sequencing error code
  docs(report): add sequence diagram for TCP+UDP lifecycle
  test(rdt): add packet-loss simulation harness
  ```

- Mỗi commit nên là công việc của **một người, một việc rõ ràng** — không
  commit hộ người khác, không gộp code nhiều người vào 1 commit.

## 3. Quy trình push / Pull Request

1. `git pull origin dev` trước khi bắt đầu code mỗi ngày.
2. Code trên branch feature của mình, commit thường xuyên.
3. `git push origin feature/<ten-module>`.
4. Mở Pull Request `feature/<ten-module>` → `dev`.
   - Mô tả ngắn: đã làm gì, test thế nào.
   - **Ít nhất 1 thành viên khác review** trước khi merge (kể cả nhóm 2
     người — người còn lại review).
5. Khi `dev` đã ổn định và test đầy đủ (Section 9 trong Guidelines.md) →
   mở PR `dev` → `main`.
6. Tag version trước buổi Oral Defense: `git tag -a v1.0-demo -m "Submission version"`.

## 4. Không được làm

- ❌ Không `git push --force` lên `dev`/`main`.
- ❌ Không commit file build (`build/`, `*.o`, `*.exe`) — thêm vào `.gitignore`.
- ❌ Không copy code của nhóm khác rồi đổi tên biến — vi phạm liêm chính học
  thuật, bị 0 điểm toàn nhóm (Section 4.4 đề bài).
- ❌ Không để 1 người push code thay mặt cả nhóm dưới account của mình —
  mỗi người **dùng chính account GitHub của mình** để commit, vì lịch sử
  commit là bằng chứng chấm điểm cá nhân.

## 5. `.gitignore` gợi ý

```
build/
*.o
*.obj
*.exe
*.out
.vscode/
.idea/
CMakeCache.txt
CMakeFiles/
*.log
```

## 6. Trước buổi bảo vệ (Oral Defense)

- [ ] Repo build sạch trên máy khác (clone mới, build lại từ đầu).
- [ ] Lịch sử commit đủ chi tiết, mỗi người có commit riêng cho module mình.
- [ ] README.md có hướng dẫn build & chạy server/client.
- [ ] Tag bản submit cuối cùng.
