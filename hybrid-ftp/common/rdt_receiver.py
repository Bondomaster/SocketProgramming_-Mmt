from .rdt_packet import pack_packet, unpack_packet, FLAG_ACK, FLAG_FIN
from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn
RECV_WINDOW = 64

def recv_file(sock, out_path):
    buffer: dict[int, bytes] = {}
    expected = 0
    peer_addr = None

    with Progress(
        TextColumn("[bold green]Downloading..."),
        BarColumn(),
        DownloadColumn(),
        transient=True
    ) as progress:
        task = progress.add_task("download", total=None)  # total=None for indeterminate progress
    with open(out_path, "wb") as out:
        while True:
            sock.settimeout(3.0) 
            try:
                data, addr = sock.recvfrom(2048)
            except Exception:
                break 
            
            peer_addr = peer_addr or addr
            seq, _, flags, payload, ok = unpack_packet(data)
            
            if not ok:
                continue   

            if flags & FLAG_FIN:
                _flush_in_order(buffer, out, expected)
                progress.update(task, completed=expected)
                ack = pack_packet(0, seq, FLAG_ACK, b"")
                sock.sendto(ack, peer_addr)
                break

            if expected <= seq < expected + RECV_WINDOW:
                buffer[seq] = payload    

            expected = _flush_in_order(buffer, out, expected)
            progress.update(task, completed=expected)
            ack = pack_packet(0, seq, FLAG_ACK, b"")
            sock.sendto(ack, peer_addr)

def _flush_in_order(buffer, out, expected):
    while expected in buffer:
        out.write(buffer.pop(expected))
        expected += 1
    return expected