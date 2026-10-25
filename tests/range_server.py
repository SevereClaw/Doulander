"""
Маленький HTTP-сервер для автопроверки на GitHub: раздаёт тестовое видео
и умеет отдавать файл кусками (Range) — как настоящие видеохостинги.

    python tests/range_server.py ПАПКА ПОРТ
"""
import functools
import http.server
import os
import re
import sys


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        self._remaining = None
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        f = open(path, "rb")
        ctype = self.guess_type(path)
        if not m or (not m[1] and not m[2]):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f
        if m[1]:
            start = int(m[1])
            end = min(int(m[2]), size - 1) if m[2] else size - 1
        else:                                   # bytes=-N — последние N байт
            start, end = max(0, size - int(m[2])), size - 1
        if start >= size or start > end:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        f.seek(start)
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        if self._remaining is None:
            return super().copyfile(source, outputfile)
        left = self._remaining
        while left > 0:
            chunk = source.read(min(65536, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    folder, port = sys.argv[1], int(sys.argv[2])
    handler = functools.partial(RangeHandler, directory=folder)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
