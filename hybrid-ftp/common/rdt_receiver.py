"""
common/rdt_receiver.py
======================
Reliable Data Transfer — Stop-and-Wait receiver (Excellent Level).

Algorithm: Stop-and-Wait
  - Receive one packet at a time.
  - Check CRC32 checksum; drop silently if corrupt (let sender timeout & retry).
  - If seq == expected_seq: write payload to file, flip expected_seq, send ACK.
  - If seq != expected_seq (duplicate, because our previous ACK was lost):
      do NOT write again, but MUST send ACK — otherwise sender retransmits forever.
  - FIN packet: ACK it and return True (success).
  - IDLE_TIMEOUT: if NO packet arrives for 30s, connection is truly dead → return False.

Return value: True  = file received completely (saw FIN)
              False = timed out / connection lost — caller should discard partial file.
"""

import socket
from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN
from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn

IDLE_TIMEOUT = 30.0   # seconds of total silence before giving up


def recv_file(sock: socket.socket, out_path) -> bool:
    """Receive a file via Stop-and-Wait RDT.

    Returns True if the transfer completed successfully (FIN received),
    False if it timed out or encountered an unrecoverable error.
    """
    expected_seq = 0
    peer_addr = None
    sock.settimeout(IDLE_TIMEOUT)

    with Progress(
        TextColumn("[bold green]Downloading..."),
        BarColumn(),
        DownloadColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("download", total=None)  # indeterminate until FIN
        bytes_written = 0

        with open(out_path, "wb") as out:
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    # 30 seconds of silence → connection is dead, NOT "transfer complete"
                    return False
                except OSError:
                    return False

                peer_addr = peer_addr or addr
                seq, _, flags, payload, ok = unpack_packet(data)

                if not ok:
                    # Corrupt packet — drop silently, do NOT send ACK
                    # Sender will timeout and retransmit
                    continue

                if flags & FLAG_FIN:
                    # Transfer complete — ACK the FIN and signal success
                    ack = pack_packet(0, seq, FLAG_ACK, b"")
                    sock.sendto(ack, peer_addr)
                    progress.update(task, completed=bytes_written)
                    return True

                if seq == expected_seq:
                    # In-order packet — write and advance
                    out.write(payload)
                    bytes_written += len(payload)
                    expected_seq = 1 - expected_seq  # alternate 0↔1
                    progress.update(task, completed=bytes_written)
                else:
                    # Duplicate packet (our previous ACK was lost in transit)
                    # Do NOT write again — but MUST ACK so sender stops retransmitting
                    pass  # fall through to ACK below

                ack = pack_packet(0, seq, FLAG_ACK, b"")
                sock.sendto(ack, peer_addr)