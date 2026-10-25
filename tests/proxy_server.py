"""
Простой HTTP-прокси для автопроверки: пересылает запросы и записывает каждый
в файл журнала — так видно, что программа действительно ходила через прокси.

    python tests/proxy_server.py ПОРТ ЖУРНАЛ
"""
import http.server
import shutil
import sys
import urllib.error
import urllib.request

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SKIP = {"proxy-connection", "connection", "keep-alive", "transfer-encoding", "host"}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def _forward(self, send_body):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{self.command} {self.path}\n")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        req = urllib.request.Request(self.path, headers=headers, method=self.command)
        try:
            resp = DIRECT.open(req, timeout=30)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            self.send_error(502, str(e))
            return
        self.send_response(resp.status if hasattr(resp, "status") else resp.code)
        for k, v in resp.headers.items():
            if k.lower() not in SKIP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        if send_body:
            shutil.copyfileobj(resp, self.wfile)

    def do_GET(self):
        self._forward(True)

    def do_HEAD(self):
        self._forward(False)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port, LOG = int(sys.argv[1]), sys.argv[2]
    open(LOG, "w").close()
    http.server.ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler).serve_forever()
