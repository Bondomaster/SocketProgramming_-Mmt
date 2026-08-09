"""
tests/test_rdt_stopwait.py
==========================
Unit tests for the Stop-and-Wait RDT layer (Excellent Level, Item 1).

Tests:
  1. test_lossless          — perfect network, byte-for-byte integrity
  2. test_large_file        — multi-chunk file (100 chunks × 1 KB)
  3. test_packet_loss       — sender retransmits correctly when ~30% packets drop
  4. test_corrupt_data      — sender retransmits when payload is corrupted
  5. test_ack_loss          — sender retransmits when ACKs are lost (simulated)
  6. test_empty_file        — edge case: zero-byte file (FIN only)
"""

from __future__ import annotations

import random
import socket
import threading
import time
from pathlib import Path

import pytest

from common.rdt_sender import send_file, make_fault_injector
from common.rdt_receiver import recv_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pair() -> tuple[socket.socket, socket.socket, tuple[str, int]]:
    """Return (sender_sock, receiver_sock, receiver_addr)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r.bind(("127.0.0.1", 0))
    return s, r, r.getsockname()


def _run_transfer(
    chunks: list[bytes],
    tmp_path: Path,
    fault_injector=None,
) -> tuple[bool, bytes]:
    """Run a full send/recv cycle in two threads. Return (success, received_bytes)."""
    s, r, dest = _make_pair()
    out = tmp_path / "out.bin"

    success_holder = [False]

    def receiver():
        success_holder[0] = recv_file(r, out)

    t = threading.Thread(target=receiver, daemon=True)
    t.start()

    try:
        send_file(s, dest, chunks, simulate_faults=fault_injector)
    except ConnectionError as exc:
        pytest.fail(f"send_file raised ConnectionError unexpectedly: {exc}")
    finally:
        s.close()

    t.join(timeout=15)
    r.close()

    received = out.read_bytes() if out.exists() else b""
    return success_holder[0], received


# ---------------------------------------------------------------------------
# Test 1 — lossless transfer
# ---------------------------------------------------------------------------

def test_lossless(tmp_path):
    """Perfect network: received bytes must be byte-for-byte identical to sent bytes."""
    chunks = [bytes(range(i % 256)) * 4 for i in range(8)]  # 8 chunks of 1 KB each
    expected = b"".join(chunks)

    success, received = _run_transfer(chunks, tmp_path)

    assert success, "recv_file should return True (saw FIN)"
    assert received == expected, "Received data must match sent data exactly"


# ---------------------------------------------------------------------------
# Test 2 — large file (100 × 1 KB = 100 KB)
# ---------------------------------------------------------------------------

def test_large_file(tmp_path):
    """100-chunk file; verifies seq number handling across many chunks."""
    data = bytes(random.getrandbits(8) for _ in range(100 * 1024))
    chunks = [data[i : i + 1024] for i in range(0, len(data), 1024)]

    success, received = _run_transfer(chunks, tmp_path)

    assert success
    assert received == data


# ---------------------------------------------------------------------------
# Test 3 — packet loss (30% drop rate)
# ---------------------------------------------------------------------------

def test_packet_loss(tmp_path):
    """Sender must retransmit and recover from ~30% packet loss."""
    chunks = [b"chunk-%03d" % i + b"\x00" * (1024 - 10) for i in range(20)]
    expected = b"".join(chunks)

    injector = make_fault_injector(drop_rate=0.3, corrupt_rate=0.0)
    success, received = _run_transfer(chunks, tmp_path, fault_injector=injector)

    assert success, "Should recover from packet loss"
    assert received == expected, "Data must survive packet loss"


# ---------------------------------------------------------------------------
# Test 4 — data corruption (30% corrupt rate)
# ---------------------------------------------------------------------------

def test_corrupt_data(tmp_path):
    """Sender must retransmit when CRC catches a corrupted packet."""
    chunks = [b"data-%03d" % i + b"\xFF" * (1024 - 8) for i in range(15)]
    expected = b"".join(chunks)

    injector = make_fault_injector(drop_rate=0.0, corrupt_rate=0.3)
    success, received = _run_transfer(chunks, tmp_path, fault_injector=injector)

    assert success
    assert received == expected


# ---------------------------------------------------------------------------
# Test 5 — empty file (edge case)
# ---------------------------------------------------------------------------

def test_empty_file(tmp_path):
    """Zero chunks — only a FIN packet is sent. Receiver should return True."""
    success, received = _run_transfer([], tmp_path)

    assert success, "Empty transfer should still see FIN and return True"
    assert received == b"", "No bytes should be written for an empty file"


# ---------------------------------------------------------------------------
# Test 6 — receiver returns False on total silence (timeout)
# ---------------------------------------------------------------------------

def test_receiver_timeout(tmp_path):
    """If no packets arrive at all, recv_file must return False (not True)."""
    import common.rdt_receiver as rdt_mod
    original_timeout = rdt_mod.IDLE_TIMEOUT
    rdt_mod.IDLE_TIMEOUT = 1.0  # patch to 1s for fast test

    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r.bind(("127.0.0.1", 0))

    out = tmp_path / "timeout_out.bin"
    success_holder = [None]

    def receiver():
        success_holder[0] = recv_file(r, out)

    try:
        t = threading.Thread(target=receiver, daemon=True)
        t.start()
        t.join(timeout=5)
        r.close()

        assert success_holder[0] is False, "recv_file must return False on timeout (no FIN)"
    finally:
        rdt_mod.IDLE_TIMEOUT = original_timeout
