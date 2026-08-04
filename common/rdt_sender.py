import socket
import time
import random
from .rdt_packet import pack_packet, unpack_packet, FLAG_FIN, FLAG_ACK

MSS = 1024        
MAX_WINDOW = 64    

def make_fault_injector(drop_rate=0.0, corrupt_rate=0.0):
    def inject(sock, pkt: bytes, addr):
        if len(pkt) > 16:
            if random.random() < drop_rate:
                return                                  
            if random.random() < corrupt_rate:
                pkt = bytearray(pkt)
                pkt[16] ^= 0xFF                         
                pkt = bytes(pkt)
        sock.sendto(pkt, addr)
    return inject

def send_file(sock, dest_addr, chunks, simulate_faults=None):
    base = 0
    next_seq_num = 0
    N = len(chunks)
    
    cwnd = 4.0 
    
    estimated_rtt = 0.5
    dev_rtt = 0.25
    timeout_interval = 1.0
    
    timers = {}            
    retransmitted = set()   
    ack_received = [False] * N
    
    retransmits = 0
    sock.setblocking(False) 
    def send_pkt(seq):
        pkt = pack_packet(seq, 0, 0, chunks[seq])
        if simulate_faults:
            simulate_faults(sock, pkt, dest_addr)
        else:
            sock.sendto(pkt, dest_addr)
        timers[seq] = time.time() 
        
    while base < N:
        while next_seq_num < base + int(cwnd) and next_seq_num < N:
            if not ack_received[next_seq_num]:
                send_pkt(next_seq_num)
            next_seq_num += 1
            
        try:
            data, _ = sock.recvfrom(2048)
            seq_num, ack_num, flags, _, ok = unpack_packet(data)
            
            if ok and (flags & FLAG_ACK):
                if ack_num >= base and ack_num < N and not ack_received[ack_num]:
                    ack_received[ack_num] = True
                    
                    if ack_num not in retransmitted and ack_num in timers:
                        sample_rtt = time.time() - timers[ack_num]
                        estimated_rtt = 0.875 * estimated_rtt + 0.125 * sample_rtt
                        dev_rtt = 0.75 * dev_rtt + 0.25 * abs(sample_rtt - estimated_rtt)
                        timeout_interval = estimated_rtt + 4 * dev_rtt
                    
                    if cwnd < MAX_WINDOW:
                        cwnd += 1.0 / cwnd 
                        
                    while base < N and ack_received[base]:
                        base += 1
                        
        except BlockingIOError:
            pass 
        current_time = time.time()
        for i in range(base, next_seq_num):
            if not ack_received[i] and i in timers:
                if current_time - timers[i] > timeout_interval:
                    retransmits += 1
                    retransmitted.add(i) 
                    send_pkt(i)
                    
                    cwnd = max(4.0, cwnd / 2.0) 
                    timeout_interval = min(2.0, timeout_interval * 2)

        time.sleep(0.001) 

    # GỬI LỜI CHÀO TẠM BIỆT (GÓI FIN)
    fin_pkt = pack_packet(0, 0, FLAG_FIN, b"")
    if simulate_faults:
        simulate_faults(sock, fin_pkt, dest_addr)
    else:
        sock.sendto(fin_pkt, dest_addr)
        
    sock.setblocking(True) 
    return retransmits