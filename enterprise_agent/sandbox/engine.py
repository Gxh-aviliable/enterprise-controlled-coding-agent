"""Small bounded Docker Engine client, Unix socket only (no SDK or shell)."""

import http.client
import json
import socket
import threading
from urllib.parse import quote


class SandboxError(RuntimeError):
    pass


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Engine:
    def __init__(self, socket_path):
        self.socket_path = socket_path

    def request(self, method, path, body=None, *, missing_ok=False, stopped_ok=False):
        conn = UnixConnection(self.socket_path)
        try:
            conn.request(
                method,
                "/v1.45" + path,
                body=json.dumps(body) if body is not None else None,
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            data = response.read(2_000_000)
            if response.status == 404 and missing_ok:
                return None
            if response.status == 409 and stopped_ok:
                return None
            if response.status >= 300:
                raise SandboxError(f"Docker {method} {path.split('?')[0]}: HTTP {response.status}")
            return json.loads(data) if data else None
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise SandboxError(f"Docker control unavailable: {type(exc).__name__}") from exc
        finally:
            conn.close()

    def remove(self, name):
        self.request("DELETE", f"/containers/{name}?force=true&v=true", missing_ok=True)

    def containers(self, deployment):
        filters = quote(json.dumps({"label": [f"enterprise.sandbox={deployment}"]}))
        return self.request("GET", f"/containers/json?all=true&filters={filters}")

    def attach(self, name, limit, timeout):
        """Attach before start so even a very fast writer cannot outrun capture."""
        return Capture(self.socket_path, name, limit, timeout)


class Capture:
    def __init__(self, socket_path, name, limit, timeout):
        self.conn = UnixConnection(socket_path, timeout=timeout + 30)
        self.out = [bytearray(), bytearray()]
        self.counts = [0, 0]
        self.limit = limit
        self.error = None
        try:
            self.conn.request(
                "POST",
                f"/v1.45/containers/{name}/attach?stream=1&stdout=1&stderr=1",
                headers={"Connection": "Upgrade", "Upgrade": "tcp"},
            )
            self.response = self.conn.getresponse()
            if self.response.status != 101:
                raise SandboxError("Docker attach upgrade failed")
        except BaseException:
            self.conn.close()
            raise
        self.thread = threading.Thread(target=self._consume, daemon=True)
        self.thread.start()

    def _consume(self):
        try:
            # 101 switches to Docker's raw multiplex protocol, not HTTP chunks.
            stream = self.response.fp
            while header := stream.read(8):
                if len(header) != 8 or header[0] not in (1, 2):
                    raise SandboxError("Malformed Docker output frame")
                index = header[0] - 1
                remaining = int.from_bytes(header[4:], "big")
                while remaining:
                    chunk = stream.read(min(remaining, 65536))
                    if not chunk:
                        raise SandboxError("Incomplete Docker output frame")
                    remaining -= len(chunk)
                    self.counts[index] += len(chunk)
                    self.out[index].extend(chunk[: max(0, self.limit - len(self.out[index]))])
        except Exception as exc:
            self.error = exc

    def finish(self):
        self.thread.join(timeout=5)
        if self.thread.is_alive() or self.error:
            raise SandboxError("Docker output capture incomplete")
        return [bytes(x).decode("utf-8", errors="replace").strip() for x in self.out], self.counts

    def close(self):
        if self.conn.sock:
            try:
                self.conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.conn.close()
        self.thread.join(timeout=1)
        self.response.close()
