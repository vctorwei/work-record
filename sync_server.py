import argparse
import json
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _set_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    # 兼容 iframe / 不同端口：尽量回显 Origin，避免部分浏览器对 null/* 的限制
    origin = handler.headers.get("Origin") or "*"
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Max-Age", "600")
    handler.send_header("Cache-Control", "no-store")


class SyncHandler(BaseHTTPRequestHandler):
    db_path: str = "workflow_system.db"

    def do_OPTIONS(self):
        self.send_response(204)
        _set_cors_headers(self)
        self.end_headers()

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/sync":
            self.send_response(404)
            _set_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            _set_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"invalid json")
            return

        username = payload.get("username")
        state = payload.get("state")

        if not username or not isinstance(username, str):
            self.send_response(400)
            _set_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"missing username")
            return

        if not isinstance(state, dict):
            self.send_response(400)
            _set_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"missing state")
            return

        state_json = json.dumps(state, ensure_ascii=False)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR REPLACE INTO user_data VALUES (?, ?, ?)",
                (username, state_json, datetime.now()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self.send_response(500)
            _set_cors_headers(self)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(200)
        _set_cors_headers(self)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        # 静默日志，避免刷屏
        return


def main():
    parser = argparse.ArgumentParser()
    # 监听 0.0.0.0 以支持局域网/外部设备访问（若只在本机使用，可改回 127.0.0.1）
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--db", default="workflow_system.db")
    args = parser.parse_args()

    SyncHandler.db_path = args.db
    httpd = ThreadingHTTPServer((args.host, args.port), SyncHandler)
    print(f"[sync_server] listening on http://{args.host}:{args.port} (db={args.db})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()


