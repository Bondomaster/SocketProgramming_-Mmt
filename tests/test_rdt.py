import socket
import threading
from common.rdt_sender import send_file
from common.rdt_receiver import recv_file
from common.rdt_sender import make_fault_injector

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

    assert out_file.read_bytes() == b"".join(chunks)   