"""Синхронизация истории с телефоном по домашнему Wi-Fi.

Программа на ПК поднимает маленький HTTP-сервер (порт 47614) и отвечает на поиск
по UDP (порт 47615). Телефон находит ПК, один раз вводит 6-значный код и получает ключ.
Дальше телефон забирает историю ПК (и сами файлы), а свою историю отправляет на ПК.

Только стандартная библиотека Python — модуль работает и в программе, и в тестах.
"""
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT = 47614
UDP_PORT = 47615
HELLO = b"DOULANDER?"


def local_addresses():
    """Адреса этого компьютера в домашней сети (для подсказки «введите вручную»)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.0.1", 9))          # пакет не уходит — так узнаём «свой» адрес
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return [ip for ip in out if is_private(ip)]


def is_private(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


class SyncServer:
    """Сервер синхронизации. Все данные берёт и отдаёт через функции программы:

    records()            -> [запись] — история ПК для телефона (у каждой есть "id")
    file_path(id)        -> путь к файлу или None
    thumb(id)            -> PNG (bytes) или None
    on_remote(dev_id, name, items) — история пришла с телефона
    on_paired(dev_id, name)        — телефон подключился
    """

    def __init__(self, key, records, file_path, thumb, on_remote, on_paired,
                 name=None, log=print, port=HTTP_PORT, udp_port=UDP_PORT, apk_info=None):
        self.key = key
        # обновления приложения для телефона: apk_info() -> {"tag", "build", "version",
        # "assets": [{"name", "size", "path"}]} или None
        self.apk_info = apk_info
        self.records, self.file_path, self.thumb = records, file_path, thumb
        self.on_remote, self.on_paired = on_remote, on_paired
        self.name = name or socket.gethostname()
        self.log = log
        self.port, self.udp_port = port, udp_port
        self.code = f"{secrets.randbelow(1000000):06d}"
        self.fails = []                 # время неудачных попыток ввода кода
        self.httpd = None
        self.udp = None
        self.error = None

    # ------------------------------------------------------------ запуск
    def start(self):
        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler())
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        except OSError as e:
            self.error = f"порт {self.port} занят: {e}"
            self.log(f"Синхронизация: {self.error}")
            return False
        try:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp.bind(("0.0.0.0", self.udp_port))
            threading.Thread(target=self._udp_loop, daemon=True).start()
        except OSError as e:
            self.log(f"Синхронизация: поиск по сети недоступен ({e}) — телефон может ввести адрес вручную")
            self.udp = None
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.udp:
            try:
                self.udp.close()
            except OSError:
                pass
            self.udp = None

    def new_code(self):
        self.code = f"{secrets.randbelow(1000000):06d}"
        return self.code

    def _udp_loop(self):
        sock = self.udp
        while sock is self.udp and sock is not None:
            try:
                data, addr = sock.recvfrom(256)
            except OSError:
                return
            if data.strip() == HELLO and is_private(addr[0]):
                reply = json.dumps({"app": "doulander", "name": self.name,
                                    "port": self.port, "v": 1}).encode()
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass

    # ------------------------------------------------------------ HTTP
    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _query(self):
                return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

            def _authed(self):
                key = self.headers.get("X-Key") or (self._query().get("key") or [""])[0]
                return bool(srv.key) and secrets.compare_digest(key, srv.key)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 20 * 1024 * 1024:
                    raise ValueError("слишком большой запрос")
                return json.loads(self.rfile.read(n) or b"{}")

            def _guard(self):
                if not is_private(self.client_address[0]):
                    self._send(403, {"error": "только домашняя сеть"})
                    return False
                return True

            def do_GET(self):
                if not self._guard():
                    return
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/hello":
                    return self._send(200, {"app": "doulander", "name": srv.name, "v": 1})
                if not self._authed():
                    return self._send(401, {"error": "нужен код"})
                if path == "/api/history":
                    return self._send(200, {"name": srv.name, "items": srv.records()})
                if path.startswith("/api/thumb/"):
                    png = srv.thumb(path.rsplit("/", 1)[1])
                    return self._send(200, png, "image/png") if png else self._send(404, {})
                if path.startswith("/api/file/"):
                    return self._file(path.rsplit("/", 1)[1])
                if path == "/api/apk":
                    info = srv.apk_info() if srv.apk_info else None
                    if not info:
                        return self._send(404, {"error": "на ПК нет APK"})
                    return self._send(200, {k: v for k, v in info.items() if k != "assets"} | {
                        "assets": [{"name": a["name"], "size": a["size"]} for a in info["assets"]]})
                if path.startswith("/api/apk/"):
                    info = srv.apk_info() if srv.apk_info else None
                    name = urllib.parse.unquote(path.rsplit("/", 1)[1])
                    a = next((a for a in (info or {}).get("assets", []) if a["name"] == name), None)
                    return self._file(None, a["path"] if a else None)
                self._send(404, {"error": "нет такого"})

            do_HEAD = do_GET

            def _file(self, fid, p=None):
                p = p or (srv.file_path(fid) if fid else None)
                if not p or not os.path.isfile(p):
                    return self._send(404, {"error": "файла нет на ПК"})
                size = os.path.getsize(p)
                start = 0
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes=") and rng[6:].split("-")[0].isdigit():
                    start = min(int(rng[6:].split("-")[0]), size)
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size - start))
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
                name = urllib.parse.quote(os.path.basename(p))
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{name}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                with open(p, "rb") as f:
                    f.seek(start)
                    while True:
                        chunk = f.read(256 * 1024)
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except OSError:
                            return

            def do_POST(self):
                if not self._guard():
                    return
                path = urllib.parse.urlparse(self.path).path
                try:
                    data = self._body()
                except (ValueError, json.JSONDecodeError) as e:
                    return self._send(400, {"error": str(e)})
                if path == "/api/pair":
                    now = time.time()
                    srv.fails = [t for t in srv.fails if now - t < 600]
                    if len(srv.fails) >= 10:
                        return self._send(429, {"error": "слишком много попыток — подождите 10 минут"})
                    if str(data.get("code", "")).strip() != srv.code:
                        srv.fails.append(now)
                        return self._send(403, {"error": "неверный код"})
                    dev = str(data.get("device") or "")[:64] or secrets.token_hex(8)
                    name = str(data.get("name") or "Телефон")[:60]
                    srv.on_paired(dev, name)
                    srv.log(f"Синхронизация: подключён {name}")
                    return self._send(200, {"key": srv.key, "name": srv.name})
                if not self._authed():
                    return self._send(401, {"error": "нужен код"})
                if path == "/api/history":
                    items = data.get("items")
                    if not isinstance(items, list):
                        return self._send(400, {"error": "нет списка"})
                    dev = str(data.get("device") or "phone")[:64]
                    srv.on_remote(dev, str(data.get("name") or "Телефон")[:60],
                                  [i for i in items if isinstance(i, dict)][:5000])
                    return self._send(200, {"ok": True, "received": len(items)})
                self._send(404, {"error": "нет такого"})

        return H
