import socket
import threading
import time
import os
from common.rdt_sender import send_file, make_fault_injector, MSS
from common.rdt_receiver import recv_file
from common.hashutil import sha256_file

def main():
    print("=== BẮT ĐẦU DEMO RELIABLE UDP & SHA-256 ===")
    
    # 1. Tạo một file giả lập có dung lượng vừa phải để test
    original_file = "test_gui.txt"
    received_file = "test_nhan.txt"
    with open(original_file, "wb") as f:
        f.write(b"Le Xuan Radiant " * 1000)
    
    # Tính mã SHA-256 file gốc
    hash_truoc = sha256_file(original_file)
    print(f"[1] Mã SHA-256 gốc: {hash_truoc}")

    # Đọc file ra thành các chunk (cục nhỏ)
    chunks = []
    with open(original_file, "rb") as f:
        while chunk := f.read(MSS):
            chunks.append(chunk)
    
    # 2. Bật Receiver (Bên nhận) chạy ngầm trên một luồng (thread) riêng
    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", 0)) # Lấy port ngẫu nhiên
    dest_addr = receiver_sock.getsockname()

    t = threading.Thread(target=recv_file, args=(receiver_sock, received_file))
    t.start()
    print(f"[2] Receiver đang lắng nghe tại {dest_addr}...")

    # 3. Khởi tạo Sender (Bên gửi) kèm Fault-Injector mô phỏng mạng dỏm
    sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # MÔ PHỎNG: Cố tình làm rớt 15% gói tin và hỏng 5% gói tin
    injector = make_fault_injector(drop_rate=0.15, corrupt_rate=0.05)
    
    print("[3] Đang gửi file qua Reliable UDP (Ép rớt mạng 15%, hỏng mạng 5%)...")
    start_time = time.time()
    
    # Gửi đi!
    retransmits = send_file(sender_sock, dest_addr, chunks, simulate_faults=injector)
    
    t.join(timeout=10) # Chờ receiver nhận xong
    end_time = time.time()

    # 4. Kiểm tra lại toàn vẹn dữ liệu
    hash_sau = sha256_file(received_file)
    print(f"[4] Mã SHA-256 nhận: {hash_sau}")
    
    print("\n=== KẾT QUẢ ===")
    print(f"⏱️ Thời gian truyền: {end_time - start_time:.2f} giây")
    print(f"🔄 Số gói bị rớt/hỏng phải truyền lại (Retransmits): {retransmits} gói")
    
    if hash_truoc == hash_sau:
        print("✅ TRẠNG THÁI: MATCH! (Dữ liệu toàn vẹn 100% dù mạng chập chờn)")
    else:
        print("❌ TRẠNG THÁI: MISMATCH! (Dữ liệu bị lỗi)")

    # Dọn dẹp file rác
    os.remove(original_file)
    os.remove(received_file)

if __name__ == "__main__":
    main()