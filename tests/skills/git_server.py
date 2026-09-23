"""Disposable real HTTPS Git backend for deterministic intranet import acceptance."""

import os
import ssl
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests.skills.test_packages import CONTENT


@contextmanager
def https_git(tmp_path):
    seed = tmp_path / "seed"
    skill = seed / "skills" / "sample"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(CONTENT)
    (skill / "reference.txt").write_text("real git resource")

    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, *args], check=True, capture_output=True, timeout=10
        )

    git("init", "-b", "main", str(seed))
    git("-C", str(seed), "add", ".")
    git("-C", str(seed), "-c", "user.name=Skill Test", "-c", "user.email=skill@example.test", "commit", "-m", "fixture")
    git("-C", str(seed), "tag", "demo-v1")
    git("clone", "--bare", str(seed), str(tmp_path / "fixture.git"))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    ca_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            self.serve_git()

        def do_POST(self):  # noqa: N802
            self.serve_git()

        def serve_git(self):
            url = urlsplit(self.path)
            if url.path.startswith("/redirect.git"):
                self.send_response(302)
                self.send_header("Location", self.path.replace("/redirect.git", "/fixture.git"))
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            env = {
                **os.environ,
                "GIT_PROJECT_ROOT": str(tmp_path),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": url.path,
                "REQUEST_METHOD": self.command,
                "QUERY_STRING": url.query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            }
            result = subprocess.run(["git", "http-backend"], env=env, input=body, capture_output=True, timeout=10)
            header, payload = result.stdout.split(b"\r\n\r\n", 1)
            headers = [line.decode().split(":", 1) for line in header.split(b"\r\n")]
            status = next((int(value.strip().split()[0]) for name, value in headers if name == "Status"), 200)
            self.send_response(status)
            for name, value in headers:
                if name != "Status":
                    self.send_header(name, value.strip())
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ca_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/fixture.git", str(ca_path), server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
