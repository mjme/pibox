#!/usr/bin/env python3
"""pibox's gatekeeper between the box and Ollama on your Mac.

The box sits on a Docker network with no internet and no route to the Mac. This proxy
is the one thing it can reach. It forwards only what pi needs to chat with the models
you have installed, and refuses everything else. In particular it refuses the Ollama
endpoints that pull, push, create, copy or delete models, because those would let the
box change your models or make your Mac's Ollama download or upload data.

Cloud models (which send prompts to ollama.com) are hidden and refused too.

It builds a fresh request to Ollama for each call instead of passing raw bytes
through, so nothing the box sends can sneak a second request past the checks.
Standard library only, so it runs from the pibox image as its own container.
"""
import http.client
import http.server
import json
import re
import sys
import threading
import time
from urllib.parse import urlsplit

UPSTREAM = ("host.docker.internal", 11434)
LISTEN = ("0.0.0.0", 11434)
MAX_BODY = 32 * 1024 * 1024        # requests bigger than this are refused
UPSTREAM_TIMEOUT = 900             # loading a big model can take a while
TAGS_TTL = 3.0                     # seconds to cache the list of local models

GET_ALLOWED = {"/api/version", "/api/tags", "/v1/models"}
POST_ALLOWED = {"/v1/chat/completions", "/api/show"}


def log(msg: str) -> None:
    print(f"pibox-proxy: {msg}", file=sys.stderr, flush=True)


def is_remote(model: dict) -> bool:
    """True for Ollama cloud models: they run on ollama.com, not on your Mac."""
    name = str(model.get("name") or model.get("model") or "")
    return bool(model.get("remote_host") or model.get("remote_model")
                or re.search(r"[:-]cloud$", name, re.I))


def upstream(method: str, path: str, body: bytes | None = None):
    conn = http.client.HTTPConnection(*UPSTREAM, timeout=UPSTREAM_TIMEOUT)
    headers = {"Accept": "application/json, text/event-stream"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=headers)
    return conn, conn.getresponse()


_tags_lock = threading.Lock()
_tags_cache: tuple[float, dict] | None = None


def local_tags() -> dict:
    """Ollama's /api/tags with cloud models removed (cached for a few seconds)."""
    global _tags_cache
    with _tags_lock:
        if _tags_cache and time.monotonic() - _tags_cache[0] < TAGS_TTL:
            return _tags_cache[1]
        conn, res = upstream("GET", "/api/tags")
        try:
            if res.status != 200:
                raise OSError(f"Ollama answered {res.status} to /api/tags")
            data = json.loads(res.read())
        finally:
            conn.close()
        models = [m for m in data.get("models", []) if isinstance(m, dict) and not is_remote(m)]
        tags = {"models": models}
        _tags_cache = (time.monotonic(), tags)
        return tags


def local_names() -> set[str]:
    names = set()
    for m in local_tags()["models"]:
        for n in (m.get("name"), m.get("model")):
            if isinstance(n, str) and n:
                names.add(n)
                if n.endswith(":latest"):
                    names.add(n[: -len(":latest")])
    return names


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pibox-proxy"
    sys_version = ""

    def log_message(self, fmt, *args):  # one line per request, to `docker logs`
        log(f"{self.command} {self.path} -> " + (fmt % args).split('" ', 1)[-1])

    # --- replies -------------------------------------------------------------
    def reply_json(self, status: int, data) -> None:
        body = (json.dumps(data) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def deny(self, why: str, status: int = 403) -> None:
        log(f"BLOCKED {self.command} {self.path}: {why}")
        self.close_connection = True
        self.reply_json(status, {"error": f"blocked by pibox: {why}"})

    def relay(self, res) -> None:
        """Copy Ollama's response back, streaming it so token-by-token output works."""
        self.send_response(res.status)
        self.send_header("Content-Type", res.getheader("Content-Type", "application/json"))
        length = res.getheader("Content-Length")
        if length is not None:
            self.send_header("Content-Length", length)
            self.end_headers()
            while chunk := res.read1(65536):
                self.wfile.write(chunk)
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := res.read1(65536):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")

    def forward(self, method: str, path: str, body: bytes | None = None) -> None:
        try:
            conn, res = upstream(method, path, body)
        except OSError as e:
            self.reply_json(502, {"error": f"pibox can't reach Ollama on your Mac: {e}"})
            return
        try:
            self.relay(res)
        finally:
            conn.close()

    # --- requests ------------------------------------------------------------
    def path_only(self) -> str:
        return urlsplit(self.path).path

    def do_GET(self):
        path = self.path_only()
        if path not in GET_ALLOWED:
            return self.deny("endpoint not allowed")
        try:
            if path == "/api/tags":
                return self.reply_json(200, local_tags())
            if path == "/v1/models":
                names = local_names()
                conn, res = upstream("GET", "/v1/models")
                try:
                    data = json.loads(res.read()) if res.status == 200 else None
                finally:
                    conn.close()
                if not isinstance(data, dict):
                    return self.reply_json(502, {"error": "unexpected answer from Ollama"})
                data["data"] = [m for m in data.get("data", [])
                                if isinstance(m, dict) and m.get("id") in names]
                return self.reply_json(200, data)
        except (OSError, ValueError) as e:
            return self.reply_json(502, {"error": f"pibox can't reach Ollama on your Mac: {e}"})
        self.forward("GET", path)

    def do_POST(self):
        path = self.path_only()
        if path not in POST_ALLOWED:
            return self.deny("endpoint not allowed")
        if self.headers.get("Transfer-Encoding"):
            return self.deny("chunked request bodies aren't supported", 411)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.deny("missing Content-Length", 411)
        if not 0 <= length <= MAX_BODY:
            return self.deny("request too large", 413)
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except ValueError:
            return self.deny("request body isn't JSON", 400)
        if not isinstance(req, dict):
            return self.deny("request body isn't a JSON object", 400)
        model = req.get("model") if "model" in req else req.get("name")
        try:
            allowed = local_names()
        except (OSError, ValueError) as e:
            return self.reply_json(502, {"error": f"pibox can't reach Ollama on your Mac: {e}"})
        if not isinstance(model, str) or model not in allowed:
            return self.deny(f"model {model!r} isn't one of your local Ollama models")
        # Re-serialise what we parsed, so Ollama sees exactly what was checked.
        self.forward("POST", path, json.dumps(req).encode())

    def _deny_other(self):
        self.deny("method not allowed", 405)

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_CONNECT = do_TRACE = _deny_other


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # pi drops idle keep-alive connections when it exits. That's normal, not an error.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    server = Server(LISTEN, Handler)
    log(f"listening on :{LISTEN[1]}, forwarding chat to Ollama at {UPSTREAM[0]}:{UPSTREAM[1]}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
