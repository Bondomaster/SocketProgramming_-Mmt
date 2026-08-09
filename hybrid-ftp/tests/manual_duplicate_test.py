"""Chạy tay: giả lập sender gửi trùng 1 gói data để test nhánh duplicate ở receiver."""
import socket
from common.protocol import recv_reply
from common.rdt_packet import pack_packet, unpack_packet, FLAG_FIN

HOST, PORT = "127.0.0.1", 2121

ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ctrl.connect((HOST, PORT))
print(recv_reply(ctrl))  # 220

def send_cmd(cmd):
    ctrl.sendall((cmd + "\r\n").encode())
    reply = recv_reply(ctrl)
    print(reply)
    return reply

send_cmd("USER admin")
send_cmd("PASS 1234")
reply = send_cmd("PASV")
# reply dạng: 227 Entering Passive Mode (127,0,0,1,p1,p2)
inside = reply.split("(")[1].split(")")[0]
parts = [int(x) for x in inside.split(",")]
data_addr = (".".join(map(str, parts[:4])), parts[4] * 256 + parts[5])

send_cmd("STOR dup_test.txt")  # server trả 150, mở data channel chờ nhận

data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
data_sock.settimeout(2.0)

payload = b"HELLO_DUPLICATE_TEST"

# --- Gửi gói seq=0 LẦN 1 ---
pkt = pack_packet(0, 0, 0, payload)
data_sock.sendto(pkt, data_addr)
ack, _ = data_sock.recvfrom(2048)
print("ACK lần 1:", unpack_packet(ack))

# --- CỐ Ý gửi lại CHÍNH gói seq=0 y hệt (giả lập ACK bị mất, sender nghĩ phải gửi lại) ---
data_sock.sendto(pkt, data_addr)
ack2, _ = data_sock.recvfrom(2048)
print("ACK lần 2 (duplicate):", unpack_packet(ack2))
# ← Ở terminal Server lúc này phải thấy dòng [DUPLICATE] in ra

# --- Gửi FIN để kết thúc sạch sẽ ---
fin = pack_packet(1, 0, FLAG_FIN, b"")
data_sock.sendto(fin, data_addr)
fin_ack, _ = data_sock.recvfrom(2048)
print("ACK FIN:", unpack_packet(fin_ack))

send_cmd("QUIT")
ctrl.close()