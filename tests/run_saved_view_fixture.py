"""Ephemeral loopback listener for the synthetic personal-protocol browser test."""

import json
import socket

import uvicorn

from tests.saved_view_fixture import create_fixture

if __name__ == "__main__":
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        app = create_fixture(f"http://127.0.0.1:{port}")
        print(json.dumps({"port": port}), flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False)).run(sockets=[listener])
