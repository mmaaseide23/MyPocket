"""Ephemeral loopback-only fixture server for the fictional browser test."""

import json
import socket

import uvicorn

from tests.offline_preview import app

if __name__ == "__main__":
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        print(json.dumps({"port": listener.getsockname()[1]}), flush=True)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        server.run(sockets=[listener])
