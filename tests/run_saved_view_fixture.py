"""Ephemeral loopback listener for the synthetic personal-protocol browser test."""

import argparse
import json
import socket

import uvicorn

from tests.saved_view_fixture import create_fixture

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; defaults to an ephemeral port")
    parser.add_argument("--origin", help="Existing approved private HTTPS origin; does not configure networking")
    arguments = parser.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", arguments.port))
        listener.listen(128)
        port = listener.getsockname()[1]
        app = create_fixture(arguments.origin or f"http://127.0.0.1:{port}")
        print(json.dumps({"port": port}), flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
                                     proxy_headers=True, forwarded_allow_ips="127.0.0.1")).run(sockets=[listener])
