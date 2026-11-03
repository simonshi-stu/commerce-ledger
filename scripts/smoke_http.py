"""启动真实本地 HTTP 服务做烟雾验证，完成后自动关闭，不留下后台服务。"""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx


def main():
    root = Path(__file__).resolve().parent.parent
    with TemporaryDirectory(prefix="commerce-http-") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment["COMMERCE_DB"] = str(Path(folder) / "http.db")
        environment["COMMERCE_API_KEY"] = "smoke-local-temporary-key"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=root, env=environment, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=flags,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                              headers={"X-API-Key": environment["COMMERCE_API_KEY"]}, timeout=1) as client:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Local server did not become ready")
                    time.sleep(0.1)
                assert response.json()["status"] == "ok"
                assert client.get("/docs").status_code == 200
                spec = client.get("/openapi.json").json()
                assert "/orders" in spec["paths"]
                created = client.post("/products", json={"sku": "HTTP-001", "name": "synthetic", "price_cents": 100})
                assert created.status_code == 201, created.text
                assert client.get("/products").json()[0]["available"] == 0
                assert client.get("/products", headers={"X-API-Key": "wrong"}).status_code == 401
                print("HTTP smoke passed: health, docs, OpenAPI, product create/read, authentication")
        finally:
            process.terminate()
            try:
                _, errors = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _, errors = process.communicate()
            if process.returncode not in (0, 1, -15):
                print("Server stopped:", process.returncode)


if __name__ == "__main__":
    main()

