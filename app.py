"""실험 화면 서버 (표준 라이브러리만 사용). 실행: python app.py [--port 8765]
브라우저에서 http://127.0.0.1:8765 를 연다."""
import argparse
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from experiment import run_one, kpi_schema          # noqa: E402
from params import schema                           # noqa: E402

RUN_LOCK = threading.Lock()     # PyPDEVS 는 한 번에 하나씩 실행


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "ui", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/schema":
            s = schema()
            s["kpis"] = kpi_schema()
            return self._send(200, json.dumps(s, ensure_ascii=False))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            with RUN_LOCK:
                res = run_one(req.get("values", {}), req.get("seed", 1), verify=req.get("verify", True))
            self._send(200, json.dumps(res, ensure_ascii=False))
        except ValueError as e:                       # 잘못된 입력값
            self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        except Exception as e:                        # 모델 오류
            traceback.print_exc()
            self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("실험 화면: http://127.0.0.1:%d  (종료: Ctrl+C)" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
