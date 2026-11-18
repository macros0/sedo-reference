"""Serve the built reference over loopback for the Chromium test suite."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class BrowserTestServer(ThreadingHTTPServer):
    # Chromium loads the catalog, edition and validator assets concurrently.
    request_queue_size = 128


def main():
    root = Path(__file__).resolve().parents[1]
    handler = partial(SimpleHTTPRequestHandler, directory=root)
    with BrowserTestServer(("127.0.0.1", 4173), handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
