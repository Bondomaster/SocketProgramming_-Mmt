"""
client/main.py
==============
Entry point for the Hybrid FTP client.

Usage
-----
    python -m client.main [--host HOST] [--port PORT]

Defaults: host=127.0.0.1, port=2121
"""

import argparse

from client.cli import run_client


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hybrid FTP Client — Basic Level")
    p.add_argument("--host", default="127.0.0.1", help="Server IP address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=2121, help="Server TCP control port (default: 2121)")
    p.add_argument("--drop-rate", type=float, default=0.0, help="Simulate packet drop rate (0.0 to 1.0)")
    p.add_argument("--corrupt-rate", type=float, default=0.0, help="Simulate packet corruption rate (0.0 to 1.0)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_client(host=args.host, port=args.port, drop_rate=args.drop_rate, corrupt_rate=args.corrupt_rate)
