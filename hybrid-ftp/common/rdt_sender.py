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
import sys
import time
import random
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN

MSS = 1024
RTO = 1.0          # Retransmission timeout (seconds) — fixed for Stop-and-Wait
MAX_RETRIES = 15   # Hard limit: after this many timeouts on one packet, raise


# ---------------------------------------------------------------------------
# Optional rich progress bar — falls back to plain-text if not installed
# ---------------------------------------------------------------------------
try:
    from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


class _PlainProgress:
    """Minimal fallback when ``rich`` is not installed."""

    def __init__(self, total: int, label: str = "Progress"):
        self._total = total
        self._label = label

    def update(self, completed: int) -> None:
        if self._total > 0:
            pct = completed * 100 // self._total
            sys.stderr.write(f"\r{self._label}: {completed}/{self._total} ({pct}%)")
            sys.stderr.flush()
            if completed >= self._total:
                sys.stderr.write("\n")

    def close(self) -> None:
        sys.stderr.write("\n")


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

    N = len(chunks)

    # --- set up progress ---
    if _HAS_RICH:
        ctx = Progress(
            TextColumn("[bold blue]Uploading..."),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=True,
        )
        progress = ctx.__enter__()
        task = progress.add_task("upload", total=N)

        def _update(completed):
            progress.update(task, completed=completed)

        def _close():
            ctx.__exit__(None, None, None)
    else:
        plain = _PlainProgress(N, "Uploading")

        def _update(completed):
            plain.update(completed)

        def _close():
            plain.close()

    try:
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
            _update(i + 1)

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
    finally:
        _close()

    return retransmits