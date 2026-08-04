import sys
sys.path.insert(0, '.')

from common.protocol import format_reply, parse_command
from server.session import Session, check_credentials
from server.command_handler import COMMANDS, dispatch
from pathlib import Path

class MockSock:
    def sendall(self, data): pass

session = Session(
    ctrl_sock=MockSock(),
    addr=('127.0.0.1', 9999),
    root=Path('./ftp_root').resolve(),
    cwd=Path('./ftp_root').resolve()
)

def check(label, code, expected):
    status = "PASS" if code == expected else f"FAIL (got {code})"
    print(f"  {label}: {status}")
    return code == expected

all_ok = True

# Protocol helpers
r = format_reply(220, "Ready")
assert r == b"220 Ready\r\n"
print("  format_reply: PASS")

cmd, args = parse_command("USER alice\r\n")
assert cmd == "USER" and args == "alice"
print("  parse_command USER: PASS")

# Auth
assert check_credentials("admin", "1234") == True
assert check_credentials("alice", "wrong") == False
print("  check_credentials: PASS")

# Commands
dispatch(session, "USER", "alice")
dispatch(session, "PASS", "secret")

print("\n--- Core Commands ---")
tests = [
    ("NOOP",        "",           200),
    ("PWD",         "",           257),
    ("TYPE A",      "A",          200),
    ("TYPE X",      "X",          504),
    ("STAT",        "",           211),
    ("SIZE sample.txt",  "sample.txt", 213),
    ("SIZE nofile.txt",  "nofile.txt", 550),
    ("MDTM sample.txt",  "sample.txt", 213),
    ("HELP",        "",           214),
    ("QUIT",        "",           221),
]

for label, args_val, expected in tests:
    cmd_name = label.split()[0]
    code, text = dispatch(session, cmd_name, args_val)
    ok = check(label, code, expected)
    all_ok = all_ok and ok

print("\n--- New Commands (Basic Level Additions) ---")

# SYST
code, _ = dispatch(session, "SYST", "")
all_ok = check("SYST", code, 215) and all_ok

# ABOR
code, _ = dispatch(session, "ABOR", "")
all_ok = check("ABOR", code, 226) and all_ok

# MODE
code, _ = dispatch(session, "MODE", "S")
all_ok = check("MODE S", code, 200) and all_ok
code, _ = dispatch(session, "MODE", "B")
all_ok = check("MODE B", code, 200) and all_ok
code, _ = dispatch(session, "MODE", "X")
all_ok = check("MODE X (invalid)", code, 504) and all_ok

# DELE — dùng file tạm để không ảnh hưởng sample.txt
tmp_file = Path('./ftp_root/tmp_test_dele.txt')
tmp_file.write_text("delete me")
code, _ = dispatch(session, "DELE", "tmp_test_dele.txt")
all_ok = check("DELE (exists)", code, 250) and all_ok
code, _ = dispatch(session, "DELE", "nonexistent_file.txt")
all_ok = check("DELE (not found)", code, 550) and all_ok

# RNFR / RNTO — tạo file tạm, đổi tên, dọn dẹp sau
src_file = Path('./ftp_root/tmp_rename_src.txt')
src_file.write_text("rename source")

# RNFR with valid file
code, _ = dispatch(session, "RNFR", "tmp_rename_src.txt")
all_ok = check("RNFR (exists)", code, 350) and all_ok

# RNTO after valid RNFR
code, _ = dispatch(session, "RNTO", "tmp_rename_dst.txt")
all_ok = check("RNTO (success)", code, 250) and all_ok

# Clean up renamed file
Path('./ftp_root/tmp_rename_dst.txt').unlink(missing_ok=True)

# RNFR with missing file
code, _ = dispatch(session, "RNFR", "no_such_file.txt")
all_ok = check("RNFR (not found)", code, 550) and all_ok

# RNTO without prior RNFR (session.rename_from was cleared)
code, _ = dispatch(session, "RNTO", "anything.txt")
all_ok = check("RNTO (no RNFR first)", code, 503) and all_ok

print()
print("Commands registered:", sorted(COMMANDS.keys()))
print()
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
