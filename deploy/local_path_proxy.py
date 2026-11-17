"""Minimal local stand-in for nginx path routing (Windows smoke test).

  /api/*   -> http://127.0.0.1:8000/*
  /evals/* -> http://127.0.0.1:8502/evals/*
  /*       -> http://127.0.0.1:8501/*

Usage:
  python deploy/local_path_proxy.py
  then open http://127.0.0.1:8080/
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "http://127.0.0.1:8000"
CHAT = "http://127.0.0.1:8501"
EVALS = "http://127.0.0.1:8502"
LISTEN = ("127.0.0.1", 8080)
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


def _target(path: str) -> str:
    if path == "/evals":
        return f"{EVALS}/evals/"
    if path.startswith("/evals/") or path.startswith("/evals?"):
        return f"{EVALS}{path}"
    if path.startswith("/api/"):
        return f"{API}/{path[len('/api/'):]}"
    if path == "/api":
        return f"{API}/"
    return f"{CHAT}{path}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        print(f"[proxy] {self.command} {self.path} -> {_target(self.path)}")

    def _proxy(self) -> None:
        url = _target(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != "host"
        }
        req = Request(url, data=body, headers=headers, method=self.command)
        try:
            with urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP_BY_HOP:
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except HTTPError as exc:
            self.send_response(exc.code)
            for k, v in exc.headers.items():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(exc.read())
        except URLError as exc:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Bad gateway: {exc}".encode())

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()


if __name__ == "__main__":
    print(f"Local path proxy on http://{LISTEN[0]}:{LISTEN[1]}/")
    print("  /        -> chat :8501")
    print("  /evals/  -> evals :8502")
    print("  /api/    -> FastAPI :8000")
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()
