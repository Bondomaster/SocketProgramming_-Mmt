"""
common/rdt_sender.py
====================
Reliable Data Transfer — Stop-and-Wait sender (Excellent Level).

Algorithm: Stop-and-Wait
  - Send exactly 1 packet at a time.
  - Wait for the matching ACK before moving to the next chunk.
  - On timeout, retransmit the SAME packet (do NOT advance seq).
  - MAX_RETRIES guards against infinite loops.
  - FIN packet is also sent with ACK-or-retransmit logic.

Seq numbers alternate 0/1 (classic Stop-and-Wait). Using monotonically
increasing numbers (0, 1, 2, ...) is also correct and slightly easier to
debug — here we use alternating 0/1 to strictly match the spec example.
"""

import socket
import time
import random
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

MSS = 1024
RTO = 1.0          # Retransmission timeout (seconds) — fixed for Stop-and-Wait
MAX_RETRIES = 15   # Hard limit: after this many timeouts on one packet, raise


def make_fault_injector(drop_rate: float = 0.0, corrupt_rate: float = 0.0):
    """Return a callable that mimics sock.sendto() but may drop/corrupt packets.

    Used ONLY for testing — not wired in production flow unless the caller
    explicitly passes the injector.
    """
    def inject(sock: socket.socket, pkt: bytes, addr):
        if len(pkt) > 16:  # only touch data packets, not control packets
            if random.random() < drop_rate:
                return  # silently drop
            if random.random() < corrupt_rate:
                pkt = bytearray(pkt)
                pkt[16] ^= 0xFF  # flip first payload byte
                pkt = bytes(pkt)
        sock.sendto(pkt, addr)
    return inject


def send_file(
    sock: socket.socket,
    dest_addr: tuple,
    chunks: list[bytes],
    simulate_faults=None,
) -> int:
    """Send *chunks* to *dest_addr* using Stop-and-Wait RDT.

    Returns the total number of retransmissions (for logging/reporting).
    Raises ConnectionError if MAX_RETRIES is exceeded for any single packet.
    """
    seq = 0
    retransmits = 0
    sock.settimeout(RTO)

    with Progress(
        TextColumn("[bold blue]Uploading..."),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("upload", total=len(chunks))

        for i, chunk in enumerate(chunks):
            pkt = pack_packet(seq, 0, 0, chunk)
            attempts = 0
            acked = False

            while not acked:
                # --- send (possibly with fault injection) ---
                if simulate_faults:
                    simulate_faults(sock, pkt, dest_addr)
                else:
                    sock.sendto(pkt, dest_addr)

                # --- wait for ACK ---
                try:
                    data, _ = sock.recvfrom(2048)
                    _, ack_num, flags, _, ok = unpack_packet(data)
                    if ok and (flags & FLAG_ACK) and ack_num == seq:
                        acked = True  # correct ACK → advance to next chunk
                    # wrong/corrupt ACK → stay in loop, resend same packet
                except socket.timeout:
                    attempts += 1
                    retransmits += 1
                    if attempts > MAX_RETRIES:
                        raise ConnectionError(
                            f"No ACK received for seq={seq} after {MAX_RETRIES} retries — transfer aborted"
                        )

            seq = 1 - seq  # alternate 0↔1
            progress.update(task, completed=i + 1)

        # --- Send FIN — also requires an ACK (Stop-and-Wait for FIN too) ---
        fin_pkt = pack_packet(seq, 0, FLAG_FIN, b"")
        attempts = 0
        acked = False
        while not acked:
            if simulate_faults:
                simulate_faults(sock, fin_pkt, dest_addr)
            else:
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
                    raise ConnectionError("No ACK for FIN packet — transfer aborted")

    return retransmits