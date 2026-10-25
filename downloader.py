"""
Downloader — графическая оболочка для yt-dlp на CustomTkinter.

Лежит в одной папке с yt-dlp.exe, ffmpeg.exe и deno.exe.
Настройки хранятся в settings.json рядом с программой.
"""
import datetime
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tempfile
import urllib.parse
import urllib.request
import zipfile
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

import phantom_style

try:                                # звук уведомления (только Windows)
    import winsound
except ImportError:
    winsound = None
try:                                # значок в трее
    import pystray
except Exception:
    pystray = None

APP_VERSION = "1.10.1"
BUILD_ID = 0                     # номер сборки — подставляет автосборка GitHub
REPO = "SevereClaw/Doulander"

# ---------------------------------------------------------------- пути
if getattr(sys, "frozen", False):           # запущено как .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:                                       # запущено как .py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)   # где лежат вшитые в exe файлы (иконка)
ICON_ICO = os.path.join(RES_DIR, "icon.ico")
ICON_PNG = os.path.join(RES_DIR, "icon.png")
SOUND_WAV = os.path.join(RES_DIR, "sound.wav")


def find_tool(name):
    """Ищет exe рядом с программой, в текущей папке, в подпапках и в PATH."""
    # сначала — папка самой программы
    places = [BASE_DIR, os.path.join(BASE_DIR, "tools"), os.path.join(BASE_DIR, "bin"),
              os.path.join(BASE_DIR, "Downloader"), os.getcwd()]
    for folder in places:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return shutil.which(name)


def find_gh():
    path = shutil.which("gh")
    if path:
        return path
    for root in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
        for sub in ("GitHub CLI", os.path.join("Programs", "GitHub CLI")):
            p = os.path.join(root, sub, "gh.exe")
            if root and os.path.isfile(p):
                return p
    return None


def launch_error_text(err):
    """Понятное описание, почему не запустился yt-dlp."""
    if isinstance(err, FileNotFoundError):
        return "не найден yt-dlp.exe (положите программу рядом с ним)"
    winerr = getattr(err, "winerror", None)
    if winerr == 225:
        return "yt-dlp.exe заблокирован антивирусом"
    if isinstance(err, PermissionError) or winerr == 5:
        return "нет доступа к yt-dlp.exe (антивирус или права)"
    if winerr in (193, 216):
        return "yt-dlp.exe повреждён или не для этой системы"
    return str(err)


YTDLP = FFMPEG = DENO = None


def refresh_tools():
    """Ищет yt-dlp, ffmpeg и deno заново (например, после докачки)."""
    global YTDLP, FFMPEG, DENO
    YTDLP = find_tool("yt-dlp.exe") or os.path.join(BASE_DIR, "yt-dlp.exe")
    FFMPEG = find_tool("ffmpeg.exe")
    # deno нужен YouTube для расшифровки ссылок; его установщик кладёт его в ~/.deno/bin
    DENO = find_tool("deno.exe") or next(
        (p for p in [os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe")]
         if os.path.isfile(p)), None)


refresh_tools()

# откуда докачивать недостающие компоненты (официальные страницы на GitHub)
TOOL_SOURCES = {
    "yt-dlp": ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
               None),
    "ffmpeg": ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl.zip", ("ffmpeg.exe", "ffprobe.exe")),
    "deno": ("https://github.com/denoland/deno/releases/latest/download/"
             "deno-x86_64-pc-windows-msvc.zip", ("deno.exe",)),
}


def missing_tools():
    return [name for name, ok in (("yt-dlp", os.path.isfile(YTDLP)), ("ffmpeg", FFMPEG),
                                  ("deno", DENO)) if not ok]


def download_to(url, dest, progress=None):
    """Скачивает файл во временный и только потом подменяет — чтобы не оставить битый exe."""
    req = urllib.request.Request(url, headers={"User-Agent": "Downloader"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dest)


def install_tool(name, progress=None):
    """Скачивает компонент в папку программы."""
    url, members = TOOL_SOURCES[name]
    if not members:
        download_to(url, os.path.join(BASE_DIR, name + ".exe"), progress)
        return
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, name + ".zip")
        download_to(url, archive, progress)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                if base in members:
                    target = os.path.join(BASE_DIR, base)
                    with z.open(info) as src, open(target + ".part", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(target + ".part", target)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
QUEUE_FILE = os.path.join(BASE_DIR, "queue.json")      # очередь — чтобы продолжить после перезапуска
HISTORY_LIMIT = 2000
# по умолчанию — «Загрузки\Downloader» пользователя (после установки программа лежит в AppData)
_user_downloads = os.path.join(os.path.expanduser("~"), "Downloads")
DEFAULT_FOLDER = os.path.join(_user_downloads, "Downloader") if os.path.isdir(_user_downloads) \
    else os.path.join(BASE_DIR, "Downloads")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # не показывать чёрные окна консоли
ANSI = re.compile(r"\x1b\[[0-9;]*m")
URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$")

VIDEO_QUALITIES = {
    "Лучшее": None,
    "2160p (4K)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
AUDIO_QUALITIES = {          # качество для yt-dlp и примерный битрейт для оценки размера
    "Лучшее": ("0", 245),
    "320 kbps": ("320K", 320),
    "192 kbps": ("192K", 192),
    "128 kbps": ("128K", 128),
}
VIDEO_FORMATS = {           # формат: (контейнер для склейки, предпочтения по расширению, перепаковка)
    "MP4": ("mp4", "ext:mp4:m4a", None),
    "MKV": ("mkv", None, "mkv"),
    # в WEBM помещаются не все кодеки — если не влезает, получится MKV
    "WEBM": ("webm/mkv", "ext:webm:webm", "webm>webm/mkv"),
}
AUDIO_FORMATS = {           # формат: (расширение, битрейт без потерь для оценки размера)
    "MP3": ("mp3", None),
    "M4A": ("m4a", None),
    "OPUS": ("opus", None),
    "FLAC": ("flac", 900),
    "WAV": ("wav", 1411),
}
NO_THUMB_FORMATS = {"WEBM", "WAV"}      # в эти форматы обложку не вшить
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
COOKIE_SOURCES = {
    "Нет": None,
    "Firefox": "firefox",
    "Chrome": "chrome",
    "Edge": "edge",
    "Яндекс": "chromium:" + os.path.join(LOCALAPPDATA, "Yandex", "YandexBrowser",
                                          "User Data", "Default"),
    "Opera": "opera",
    "Brave": "brave",
    "Файл cookies.txt…": "file",
}
PARALLEL_CHOICES = ["1", "2", "3", "4", "5"]

# раскладка по папкам: название -> (шаблон папки yt-dlp, пример для подсказки)
ORGANIZE = {
    "Не раскладывать": ("", ""),
    "По каналу": ("%(uploader,channel|Без автора)s/", "Канал\\"),
    "По сайту": ("%(extractor_key|Другое)s/", "Youtube\\"),
    "По сайту и каналу": ("%(extractor_key|Другое)s/%(uploader,channel|Без автора)s/",
                          "Youtube\\Канал\\"),
    "По месяцу": ("%(upload_date>%Y-%m|Без даты)s/", "2026-10\\"),
}
# имя файла: название -> (шаблон yt-dlp, пример)
NAME_TEMPLATES = {
    "Название": ("%(title)s", "Название видео"),
    "Канал - Название": ("%(uploader,channel&{} - |)s%(title)s", "Канал - Название видео"),
    "Дата - Название": ("%(upload_date>%Y-%m-%d&{} - |)s%(title)s", "2026-10-26 - Название видео"),
    "Номер - Название": ("%(playlist_index&{:02d} - |)s%(title)s", "01 - Название видео"),
    "Название [id]": ("%(title)s [%(id)s]", "Название видео [dQw4w9WgXcQ]"),
    "Свой шаблон": (None, None),
}
EXAMPLE_FIELDS = {"title": "Название видео", "uploader": "Канал", "channel": "Канал",
                  "upload_date": "20261026", "id": "dQw4w9WgXcQ", "playlist_index": "1",
                  "playlist_title": "Плейлист", "extractor_key": "Youtube", "ext": "mp4"}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ---------- прокси (например, от VPN-программы)
PROXY_MODES = ["Нет", "Только YouTube", "Все сайты"]
YOUTUBE_HOST = re.compile(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|ytimg\.com|"
                          r"googlevideo\.com)$", re.I)
# порты, на которых обычно открывают локальный прокси VPN-программы
PROXY_PORTS = [
    (10808, "v2rayN"), (10809, "v2rayN"), (2080, "Nekoray / Nekobox"), (12334, "Hiddify"),
    (7890, "Clash"), (7897, "Clash Verge"), (7891, "Clash"), (20170, "v2rayA"),
    (20171, "v2rayA"), (1080, "прокси"), (1081, "прокси"), (8889, "прокси"), (9050, "Tor"),
]

DEFAULTS = {
    "folder": DEFAULT_FOLDER,
    "mode": "Видео",
    "video_quality": "1080p",
    "audio_quality": "Лучшее",
    "video_format": "MP4",
    "audio_format": "MP3",
    "cookies": "Нет",
    "cookies_file": "",
    "exact_cut": True,
    "playlist": False,
    "subs": False,
    "embed": True,
    "theme": "dark",
    "style": "phantom",     # оформление: phantom (в стиле Persona 5) / classic
    "parallel": "3",        # сколько ссылок качать одновременно
    "fragments": 8,         # сколько кусков одного видео качать параллельно
    "clip_watch": False,    # ловить ссылки из буфера обмена
    "check_updates": True,  # проверять обновления при запуске
    "tray": True,           # крестик сворачивает в трей
    "sound": True,          # звук и уведомление по окончании
    "autostart": False,     # запускать вместе с Windows (свёрнутой в трей)
    "organize": "Не раскладывать",
    "name_template": "Название",
    "custom_template": "%(uploader)s - %(title)s",
    "sponsorblock": False,  # вырезать рекламные вставки (YouTube)
    "chapters": False,      # разбивать на отдельные файлы по главам
    "square_cover": True,   # музыка: квадратная обложка
    "normalize": False,     # музыка: выравнивать громкость
    "lyrics": False,        # музыка: тексты песен (.lrc рядом с треком)
    "proxy_mode": "Нет",    # Нет / Только YouTube / Все сайты
    "proxy": "",            # например socks5://127.0.0.1:10808
    "auto_ytdlp": True,     # тихо обновлять yt-dlp раз в день
    "ytdlp_checked": 0,     # когда последний раз проверяли (время в секундах)
}

ACCENT = "#E5484D"
ACCENT_HOVER = "#C93C41"
GREEN = ("#1a7f37", "#3fb950")
GRAY_TEXT = ("gray40", "gray60")
THUMB_W, THUMB_H = 128, 72

# Коды клавиш Windows не зависят от раскладки: V=86, C=67, X=88, A=65
CTRL_KEYCODES = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(items):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items[:HISTORY_LIMIT], f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def play_sound():
    if winsound and os.path.isfile(SOUND_WAV):
        try:
            winsound.PlaySound(SOUND_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        except RuntimeError:
            pass
    return False


def describe_opts(opts):
    if opts.get("mode") == "Аудио":
        return opts.get("fmt", "MP3")
    return f"{opts.get('quality', '').split(' ')[0]} {opts.get('fmt', 'MP4')}".strip()


def clean_custom_template(text):
    """Свой шаблон имени: убираем расширение и попытки выйти за пределы папки."""
    text = (text or "").strip().replace("\\", "/")
    text = re.sub(r"\.?%\(ext\)s$", "", text)
    parts = [p for p in text.split("/") if p not in ("", ".", "..") and ":" not in p]
    return "/".join(parts) or None


def output_stem(opts):
    """Путь файла без расширения (относительно папки загрузок) — шаблон yt-dlp."""
    lay = opts.get("layout") or {}
    folder = ORGANIZE.get(lay.get("org"), ("", ""))[0]
    if opts.get("playlist"):
        folder += "%(playlist_title&{}/|)s"
    name = NAME_TEMPLATES.get(lay.get("name"), (None, None))[0]
    if lay.get("name") == "Свой шаблон":
        name = clean_custom_template(lay.get("custom"))
    return folder + (name or "%(title)s")


def example_path(org, name, custom, ext="mp4"):
    folder = ORGANIZE.get(org, ("", ""))[1]
    if name == "Свой шаблон":
        tpl = clean_custom_template(custom) or "%(title)s"
        sample = re.sub(r"%\((\w+)[^)]*\)[sd]",
                        lambda m: EXAMPLE_FIELDS.get(m.group(1), "…"), tpl).replace("/", "\\")
    else:
        sample = NAME_TEMPLATES.get(name, ("", "Название видео"))[1]
    return f"{folder}{sample}.{ext}"


def set_autostart(enabled):
    """Добавляет/убирает программу из автозагрузки Windows. True — получилось."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "Downloader", 0, winreg.REG_SZ,
                                  f'"{sys.executable}" --tray')
            else:
                try:
                    winreg.DeleteValue(key, "Downloader")
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def load_queue():
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def normalize_proxy(text):
    """«127.0.0.1:10808» -> «socks5://127.0.0.1:10808». Пусто -> ""."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "socks5://" + text
    return text


SEARCH_PREFIX = "ytsearch1:"          # первый результат поиска на YouTube


def is_search(url):
    return url.startswith("ytsearch")


def display_url(url):
    return "🔍 " + url.split(":", 1)[1] if is_search(url) else url


def parse_input(text, allow_search=True):
    """Текст (одна строка или список) -> ссылки для yt-dlp.
    Строка со ссылкой — ссылки; строка без ссылки — поиск песни/видео на YouTube."""
    result = []
    for line in text.splitlines():
        line = line.strip().strip("\"'«»")
        if not line:
            continue
        urls = re.findall(r"https?://[^\s\"'<>]+", line)
        if urls:
            result += urls
        elif allow_search:
            query = re.sub(r"^\s*\d{1,3}[.)]\s+", "", line).strip()   # «1. Песня» -> «Песня»
            if query:
                result.append(SEARCH_PREFIX + query)
    return result


def is_youtube(url):
    if is_search(url):
        return True
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return bool(YOUTUBE_HOST.search(host))


def detect_local_proxy():
    """Ищет запущенный локальный прокси VPN-программы. Возвращает (адрес, название) или None."""
    for port, name in PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3) as c:
                c.settimeout(1)
                c.sendall(b"\x05\x01\x00")                 # приветствие SOCKS5
                if c.recv(2) == b"\x05\x00":
                    return f"socks5://127.0.0.1:{port}", name
        except OSError:
            pass
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3) as c:
                c.settimeout(2)
                c.sendall(b"CONNECT www.youtube.com:443 HTTP/1.1\r\n"
                          b"Host: www.youtube.com:443\r\n\r\n")
                if c.recv(64).startswith(b"HTTP/1."):
                    return f"http://127.0.0.1:{port}", name
        except OSError:
            pass
    return None


def url_opener(proxy=None):
    """urllib с прокси (http или socks) — для обложек и проверки связи."""
    if not proxy:
        return urllib.request.build_opener()
    parts = urllib.parse.urlsplit(proxy)
    if parts.scheme.startswith("socks"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
        except ImportError:
            return urllib.request.build_opener()
        kind = socks.SOCKS4 if parts.scheme.startswith("socks4") else socks.SOCKS5
        return urllib.request.build_opener(SocksiPyHandler(
            kind, parts.hostname, parts.port or 1080, rdns=True,
            username=parts.username, password=parts.password))
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def check_youtube(proxy=None):
    """Открывается ли YouTube (через прокси или напрямую). Возвращает (ok, текст)."""
    try:
        req = urllib.request.Request("https://www.youtube.com/generate_204",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with url_opener(proxy).open(req, timeout=10) as r:
            ok = r.status in (200, 204)
            return ok, "YouTube открывается" if ok else f"ответ {r.status}"
    except Exception as e:
        return False, str(getattr(e, "reason", e))[:120]


def parse_time(text):
    """'1:20', '01:02:03', '80', '1:20.5' -> секунды. Пусто -> None. Ошибка -> ValueError."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(text)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def fmt_time(sec):
    sec = float(sec)
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    frac = sec - int(sec)
    tail = f"{s:02d}" + (f"{frac:.1f}"[1:] if frac >= 0.05 else "")
    return f"{h}:{m:02d}:{tail}" if h else f"{m}:{tail}"


STYLES = {"Phantom (в стиле Persona 5)": "phantom", "Классическое": "classic"}


class C:
    """Цвета интерфейса (классическое оформление; Phantom переопределяет ниже)."""
    CARD = ("gray90", "gray17")
    CARD_BORDER = 0
    BADGE = ("gray80", "gray25")
    BADGE_TEXT = ("gray10", "gray90")
    ICON_HOVER = ("gray80", "gray28")
    ICON_TEXT = ("gray30", "gray70")
    OUTLINE_TEXT = ("gray20", "gray80")
    MUTED = ("gray50", "gray50")
    TEXT = ("gray10", "gray90")
    BTN2 = ("gray75", "gray25")
    BTN2_HOVER = ("gray65", "gray30")
    BTN2_TEXT = ("gray10", "gray90")
    BANNER = ("#FDECEC", "#3A1D1F")
    FOLDER_TEXT = ("gray30", "gray70")


def R(radius):
    """Скругление углов: в Phantom углы острые."""
    return 0 if PHANTOM else radius


def title_font(size):
    """Шрифт заголовков: в Phantom — узкий, жирный, наклонный."""
    if PHANTOM:
        return ctk.CTkFont(family="Bahnschrift", size=size + 2, weight="bold", slant="italic")
    return ctk.CTkFont(size=size, weight="bold")


def caps(text):
    return text.upper() if PHANTOM else text


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


NO_SAVE = False      # во время самопроверки ничего не записываем рядом с программой


STYLE = "phantom"
PHANTOM = True
FORCE_STYLE = None      # для самопроверки: открыть окно в заданном оформлении


def init_style():
    """Читает оформление из настроек (до создания окон) и подставляет цвета."""
    global STYLE, PHANTOM, ACCENT, ACCENT_HOVER, GREEN, GRAY_TEXT
    STYLE = FORCE_STYLE or load_settings().get("style", "phantom")
    PHANTOM = STYLE == "phantom"
    if PHANTOM:
        ps = phantom_style
        ACCENT, ACCENT_HOVER = ps.RED, ps.RED_HOVER
        GREEN = ("#3DDC84", "#3DDC84")
        GRAY_TEXT = (ps.SOFT, ps.SOFT)
        C.CARD = (ps.BLACK2, ps.BLACK2)
        C.CARD_BORDER = 2
        C.BADGE = (ps.RED, ps.RED)
        C.BADGE_TEXT = (ps.WHITE, ps.WHITE)
        C.ICON_HOVER = (ps.RED, ps.RED)
        C.ICON_TEXT = (ps.WHITE, ps.WHITE)
        C.OUTLINE_TEXT = (ps.WHITE, ps.WHITE)
        C.MUTED = ("#8A8A8A", "#8A8A8A")
        C.TEXT = (ps.WHITE, ps.WHITE)
        C.BTN2 = (ps.WHITE, ps.WHITE)
        C.BTN2_HOVER = ("#FFC9CE", "#FFC9CE")
        C.BTN2_TEXT = ("#000000", "#000000")
        C.BANNER = (ps.BLACK3, ps.BLACK3)
        C.FOLDER_TEXT = (ps.WHITE, ps.WHITE)


def restart_program(app):
    """Перезапуск — новое оформление применяется только к новым окнам."""
    args = [sys.executable] if getattr(sys, "frozen", False) else \
        [sys.executable, os.path.abspath(__file__)]
    subprocess.Popen(args, cwd=BASE_DIR)
    app.quit_app()


def save_settings(s):
    if NO_SAVE:
        return
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- команды yt-dlp
def format_args(opts):
    if opts["mode"] == "Аудио":
        return ["-f", "ba/b"]
    h = VIDEO_QUALITIES[opts["quality"]]
    sort = [f"res:{h}" if h else "res"]
    ext_pref = VIDEO_FORMATS[opts["fmt"]][1]
    if ext_pref:
        sort.append(ext_pref)
    return ["-f", "bv*+ba/b", "-S", ",".join(sort)]


def common_args(opts=None):
    args = []
    if opts and opts.get("cookies"):
        args += opts["cookies"]
    if opts and opts.get("proxy_url"):
        args += ["--proxy", opts["proxy_url"]]
    if FFMPEG:
        args += ["--ffmpeg-location", os.path.dirname(FFMPEG)]
    if DENO:
        args += ["--js-runtimes", f"deno:{DENO}"]
    return args


def build_command(url, opts, folder, fragments=8):
    """Команда для скачивания одной ссылки."""
    cmd = [
        YTDLP, url,
        "-P", folder,
        "--newline", "--progress", "--no-simulate",
        "--progress-template",
        "download:[P]%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--print", "before_dl:[T]%(title)s",
        "--print", "after_move:[F]%(filepath)s",
        # --- скорость
        "-N", str(fragments),               # качать куски видео параллельно
        "--http-chunk-size", "10M",         # обходит ограничение скорости YouTube
        "--retries", "10", "--fragment-retries", "10",
        "--socket-timeout", "15",
    ]
    # плейлист складывается в отдельную папку с его названием (см. output_stem)
    cmd += ["--yes-playlist" if opts["playlist"] else "--no-playlist",
            "-o", output_stem(opts) + ".%(ext)s"]

    cmd += format_args(opts)
    if opts["mode"] == "Аудио":
        ext, lossless = AUDIO_FORMATS[opts["fmt"]]
        cmd += ["-x", "--audio-format", ext]
        if not lossless:
            cmd += ["--audio-quality", AUDIO_QUALITIES[opts["quality"]][0]]
    else:
        merge, _, remux = VIDEO_FORMATS[opts["fmt"]]
        cmd += ["--merge-output-format", merge]
        if remux:
            # если сайт отдал готовый файл без склейки — всё равно перепаковать в нужный формат
            cmd += ["--remux-video", remux]
        if opts["subs"]:
            cmd += ["--write-subs", "--sub-langs", "ru.*,en.*", "--embed-subs"]
    if opts["embed"]:
        if opts["fmt"] not in NO_THUMB_FORMATS:
            cmd += ["--embed-thumbnail"]
            if opts["mode"] == "Аудио" and opts.get("square"):
                # обложка 16:9 -> квадрат по центру, как у альбома.
                # jpg превращаем в png, остальное в jpg — иначе yt-dlp не трогает картинку
                cmd += ["--convert-thumbnails", "webp>jpg/png>jpg/jpg>png",
                        "--ppa", "ThumbnailsConvertor:-qmin 1 -q:v 1 -vf "
                                 "crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""]
        cmd += ["--embed-metadata"]
    if opts["mode"] == "Аудио" and opts.get("lyrics"):
        # настоящие (не автоматические) субтитры клипа -> файл .lrc с таймингами
        cmd += ["--write-subs", "--sub-langs", "ru.*,en.*", "--convert-subs", "lrc"]
    if opts.get("sponsor"):
        # реклама, самореклама, «подпишитесь»; для музыки — ещё и не-музыкальные куски
        cats = "sponsor,selfpromo,interaction"
        if opts["mode"] == "Аудио":
            cats += ",music_offtopic"
        cmd += ["--sponsorblock-remove", cats]
    if opts.get("chapters"):
        # главы — в папку с тем же именем, что и целый файл (см. finalize_chapters)
        cmd += ["--split-chapters", "-o",
                f"chapter:{output_stem(opts)}/%(section_number)02d - %(section_title)s.%(ext)s"]
    if opts.get("cut"):
        start, end = opts["cut"]
        cmd += ["--download-sections",
                f"*{fmt_time(start or 0)}-{fmt_time(end) if end else 'inf'}"]
        if opts.get("exact"):
            cmd += ["--force-keyframes-at-cuts"]     # точно по секундам, но медленнее
    return cmd + common_args(opts)


def preview_command(url, opts):
    """Команда, которая только получает информацию о видео (без скачивания)."""
    cmd = [YTDLP, url, "-J", "--no-warnings", "--socket-timeout", "15"]
    if opts["playlist"]:
        cmd += ["--yes-playlist", "--flat-playlist"]
    else:
        cmd += ["--no-playlist"]
    return cmd + format_args(opts) + common_args(opts)


# ---------------------------------------------------------------- превью
def human_size(n):
    if not n:
        return None
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024


def human_duration(sec):
    if not sec:
        return None
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pick_thumbnail(info):
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    sized = sorted((t for t in thumbs if t.get("width")), key=lambda t: t["width"])
    for t in sized:
        if t["width"] >= 320:
            return t["url"]
    if sized:
        return sized[-1]["url"]
    return info.get("thumbnail") or (thumbs[-1]["url"] if thumbs else None)


def summarize(info, opts):
    """Достаёт из ответа yt-dlp то, что показываем в карточке."""
    if info.get("_type") == "playlist":
        n = info.get("playlist_count") or len(info.get("entries") or [])
        parts = ["Плейлист", f"{n} видео", info.get("uploader") or info.get("channel")]
        entries = info.get("entries") or [{}]
        return {"title": info.get("title") or "Плейлист",
                "meta": "   •   ".join(p for p in parts if p),
                "thumb": pick_thumbnail(info) or pick_thumbnail(entries[0] or {})}

    duration = info.get("duration")
    if opts["mode"] == "Аудио":
        kbps = AUDIO_FORMATS[opts["fmt"]][1] or AUDIO_QUALITIES[opts["quality"]][1]
        size = duration * kbps * 125 if duration else None
        quality = opts["fmt"]
    else:
        formats = info.get("requested_formats") or [info]
        size = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in formats)
        h = info.get("height") or max((f.get("height") or 0) for f in formats)
        quality = f"{h}p {opts['fmt']}" if h else opts["fmt"]
    dur_text = human_duration(duration)
    if opts.get("cut") and duration:
        start, end = opts["cut"]
        part = max(0, min(end or duration, duration) - (start or 0))
        if size:
            size = size * part / duration
        dur_text = f"фрагмент {human_duration(part) or '0:00'} из {dur_text}"
    size_text = human_size(size)
    chapters = len(info.get("chapters") or [])
    if chapters:
        n10, n100 = chapters % 10, chapters % 100
        word = "глава" if n10 == 1 and n100 != 11 else \
            "главы" if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else "глав"
        ch = f"{chapters} {word}"
        dur_text = f"{dur_text}   •   {ch}" if dur_text else ch
    parts = [info.get("uploader") or info.get("channel"), dur_text,
             f"≈ {size_text}" if size_text else None, quality]
    return {"title": info.get("title"),
            "meta": "   •   ".join(p for p in parts if p),
            "thumb": pick_thumbnail(info)}


def load_thumbnail(url, proxy=None):
    """Скачивает обложку и делает из неё картинку 16:9 со скруглёнными углами."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with url_opener(proxy).open(req, timeout=10) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    w, h = THUMB_W * 2, THUMB_H * 2            # вдвое больше — чётче на экранах с масштабом
    src_ratio, dst_ratio = img.width / img.height, w / h
    if src_ratio > dst_ratio:                  # обрезаем лишнее по краям
        nw = int(img.height * dst_ratio)
        img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
    else:
        nh = int(img.width / dst_ratio)
        img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
    img = img.resize((w, h), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=16, fill=255)
    img.putalpha(mask)
    return img


AUDIO_ENCODERS = {      # чем перекодировать после выравнивания громкости
    "MP3": ["-c:a", "libmp3lame", "-q:a", "0", "-ar", "44100"],
    "M4A": ["-c:a", "aac", "-b:a", "256k", "-ar", "44100"],
    "OPUS": ["-c:a", "libopus", "-b:a", "192k", "-ar", "48000"],
    "FLAC": ["-c:a", "flac", "-ar", "44100"],
    "WAV": ["-c:a", "pcm_s16le", "-ar", "44100"],
}
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"      # уровень как у Spotify / YouTube Music


def audio_files(paths):
    """Все аудиофайлы: сами файлы и содержимое папок (после разбивки по главам)."""
    exts = tuple("." + v[0] for v in AUDIO_FORMATS.values())
    result = []
    for p in paths:
        if not os.path.exists(p):
            # Windows обрезает пробелы и точки по краям имени — ищем файл с «чистым» именем
            folder, name = os.path.split(p)
            stem, ext = os.path.splitext(name)
            alt = os.path.join(folder, stem.strip(" .") + ext)
            if os.path.exists(alt):
                p = alt
        if os.path.isdir(p):
            result += [os.path.join(p, f) for f in sorted(os.listdir(p))
                       if f.lower().endswith(exts)]
        elif os.path.isfile(p) and p.lower().endswith(exts):
            result.append(p)
    return result


def tidy_lyrics(files):
    """«Трек.ru.lrc» -> «Трек.lrc» (так его находят Poweramp, foobar и др.).
    Если есть несколько языков — берём русский, потом английский."""
    found = 0
    for f in files:
        stem = os.path.splitext(f)[0]
        folder, base = os.path.split(stem)
        try:
            names = os.listdir(folder or ".")
        except OSError:
            continue
        cands = [n for n in names if n.startswith(base + ".") and n.lower().endswith(".lrc")
                 and n != base + ".lrc"]
        if not cands:
            continue
        cands.sort(key=lambda n: (0 if ".ru" in n[len(base):] else
                                  1 if ".en" in n[len(base):] else 2, n))
        try:
            os.replace(os.path.join(folder, cands[0]), stem + ".lrc")
            for extra in cands[1:]:
                os.remove(os.path.join(folder, extra))
            found += 1
        except OSError:
            pass
    return found


def normalize_audio(path, fmt):
    """Приводит громкость трека к общему уровню. Обложка и теги сохраняются."""
    root, ext = os.path.splitext(path)
    tmp = f"{root}.norm{ext}"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", path,
           "-map", "0", "-map_metadata", "0", "-c", "copy",
           *AUDIO_ENCODERS.get(fmt, AUDIO_ENCODERS["MP3"]), "-af", LOUDNORM, tmp]
    p = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=NO_WINDOW)
    if p.returncode != 0 or not os.path.isfile(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip()[-300:])
    os.replace(tmp, path)


def postprocess_audio(paths, opts, status=None):
    """После скачивания: тексты песен и выравнивание громкости. Возвращает заметки для журнала."""
    notes = []
    if opts.get("mode") != "Аудио":
        return notes
    files = audio_files(paths)
    if opts.get("lyrics"):
        n = tidy_lyrics(files)
        notes.append(f"тексты песен: {n}" if n else "у этого видео нет субтитров с текстом")
    if opts.get("normalize") and FFMPEG:
        for i, f in enumerate(files, 1):
            if status:
                status(f"Выравниваю громкость… {i}/{len(files)}")
            try:
                normalize_audio(f, opts.get("fmt", "MP3"))
            except Exception as e:
                notes.append(f"громкость не выровнена ({os.path.basename(f)}): {e}")
        notes.append(f"громкость выровнена: {len(files)}")
    return notes


def send_to_trash(path):
    """Отправляет файл или папку в Корзину Windows (можно восстановить)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = \
            3, 0x4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=os.path.abspath(path) + "\0",
                             fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                             FOF_NOERRORUI)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or \
                op.fAnyOperationsAborted:
            raise OSError(f"не удалось удалить {path}")
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def companion_files(path):
    """Файлы, которые относятся к треку: текст песни .lrc рядом."""
    if not path or os.path.isdir(path):
        return []
    lrc = os.path.splitext(path)[0] + ".lrc"
    return [lrc] if os.path.isfile(lrc) else []


def delete_download(app, path, title):
    """Спрашивает и удаляет файл (или папку с главами) в Корзину. True — удалён."""
    if not path or not os.path.exists(path):
        app.set_main_status("Файл уже удалён или перемещён", ACCENT)
        return False
    what = "папку с главами" if os.path.isdir(path) else "файл"
    if not messagebox.askyesno(
            "Удалить файл",
            f"Удалить {what} в Корзину?\n\n{title}\n{path}", parent=app):
        return False
    try:
        for p in [path] + companion_files(path):
            send_to_trash(p)
    except OSError as e:
        app.set_main_status(f"Не удалось удалить: {e}", ACCENT)
        return False
    app.set_main_status(f"🗑 Удалено в Корзину: {os.path.basename(path)}")
    return True


def bind_right_click(widget, handler):
    """ПКМ по карточке и по всему, что внутри неё."""
    stack = [widget]
    while stack:
        w = stack.pop()
        try:
            tk.Misc.bind(w, "<Button-3>", handler, add="+")
        except tk.TclError:
            pass
        stack.extend(w.winfo_children())


def finalize_chapters(files):
    """Если видео разбилось на главы, удаляет целый файл и возвращает папку с главами."""
    result = None
    for f in files:
        folder = os.path.splitext(f)[0]
        if os.path.isdir(folder) and os.listdir(folder) and os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                pass
            result = folder
    return result


NET_ERRORS = ("timed out", "timeout", "connection", "unable to download webpage", "getaddrinfo",
              "ssl", "remote end closed", "unreachable", "reset by peer", "proxy")


def explain_error(err, code, url="", proxy=None):
    """Короткое понятное описание ошибки yt-dlp."""
    low = err.lower()
    if any(k in low for k in NET_ERRORS):
        if proxy:
            return "Прокси не отвечает — проверьте, что VPN-программа запущена (⚙ → Сеть)"
        if is_youtube(url):
            return "YouTube не открывается напрямую — включите прокси: ⚙ Настройки → Сеть"
        return "Нет связи с сайтом — проверьте интернет"
    if "cookie" in low:
        return ("Не удалось взять cookies: закройте браузер полностью "
                "или выберите Firefox / файл cookies.txt")
    if "sign in" in low or "age" in low and "confirm" in low or "login" in low \
            or "private" in low or "members-only" in low:
        return "Нужен вход в аккаунт: выберите браузер в «Cookies», где вы залогинены"
    if "unsupported url" in low:
        return "Этот сайт не поддерживается"
    if "http error 429" in low:
        return "Сайт временно ограничил скачивание — попробуйте позже"
    return "Ошибка: " + (err[:80] or f"код {code}")


def kill_tree(proc):
    """Останавливает yt-dlp вместе с запущенным им ffmpeg."""
    if proc and proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.kill()


# ================================================================ элемент очереди
class QueueItem(ctk.CTkFrame):
    def __init__(self, master, app, url, opts):
        super().__init__(master, corner_radius=R(10), fg_color=C.CARD,
                         border_width=C.CARD_BORDER, border_color=ACCENT)
        self.app = app
        self.url = url
        self.opts = opts
        self.st = "wait"             # wait / run / done / error / cancel
        self.frac = 0.0              # прогресс 0..1
        self.batch = False           # входит в текущую пачку загрузок
        self.last_text = ""
        self.file_path = None
        self.thumb_image = None
        self.full_title = None

        self.grid_columnconfigure(1, weight=1)

        if opts["mode"] == "Аудио":
            self.tag = opts["fmt"]
        else:
            self.tag = opts["quality"].split(" ")[0] + "\n" + opts["fmt"]
        if opts["playlist"]:
            self.tag += "\nплейлист"
        if opts.get("cut"):
            start, end = opts["cut"]
            self.tag += f"\n✂ {fmt_time(start or 0)}–{fmt_time(end) if end else 'конец'}"
        self.thumb = ctk.CTkLabel(self, text=self.tag, width=THUMB_W, height=THUMB_H,
                                  corner_radius=R(8), fg_color=C.BADGE,
                                  text_color=C.BADGE_TEXT,
                                  font=ctk.CTkFont(size=12, weight="bold"))
        self.thumb.grid(row=0, column=0, rowspan=4, padx=(8, 12), pady=8)

        self.title_lbl = ctk.CTkLabel(self, text=display_url(url), anchor="w",
                                      font=ctk.CTkFont(size=13, weight="bold"))
        self.title_lbl.grid(row=0, column=1, sticky="ew", pady=(8, 0))

        self.meta_lbl = ctk.CTkLabel(self, text="Получаю информацию…", anchor="w",
                                     text_color=GRAY_TEXT, font=ctk.CTkFont(size=12))
        self.meta_lbl.grid(row=1, column=1, sticky="ew")

        self.status_lbl = ctk.CTkLabel(self, text="В очереди", anchor="w",
                                       text_color=GRAY_TEXT, font=ctk.CTkFont(size=12))
        self.status_lbl.grid(row=2, column=1, sticky="ew")

        self.bar = ctk.CTkProgressBar(self, height=4, progress_color=ACCENT)
        self.bar.set(0)
        self.bar.grid(row=3, column=1, sticky="ew", pady=(2, 0))
        # отступ снизу — чтобы текст не закрывал рамку карточки, когда полоски нет
        self.grid_rowconfigure(4, minsize=10)
        self.bar.grid_remove()

        small = dict(width=30, height=30, fg_color="transparent",
                     hover_color=C.ICON_HOVER, text_color=C.ICON_TEXT)
        self.pbtn = ctk.CTkButton(self, text="⏸", command=lambda: self.app.toggle_pause(self),
                                  **small)
        self.pbtn.grid(row=0, column=2, rowspan=4, padx=(8, 0))
        self.btn = ctk.CTkButton(self, text="✕", command=self.on_button, **small)
        self.btn.grid(row=0, column=3, rowspan=4, padx=(0, 8))
        self.after(100, lambda: bind_right_click(self, self.show_menu))
        self.pause_requested = False     # пауза нажата во время скачивания
        self.user_paused = False         # поставлена на паузу вручную (кнопкой ⏸)

    def set_title(self, text):
        if text:
            self.full_title = text
            self.title_lbl.configure(text=text if len(text) < 64 else text[:61] + "…")

    def set_meta(self, text, color=None):
        self.meta_lbl.configure(text=text, text_color=color or GRAY_TEXT)

    def set_thumb(self, pil_image):
        self.thumb_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image,
                                        size=(THUMB_W, THUMB_H))
        self.thumb.configure(image=self.thumb_image, text="", fg_color="transparent")

    def set_status(self, text, color=None):
        self.last_text = text
        self.status_lbl.configure(text=text, text_color=color or GRAY_TEXT)

    def set_progress(self, frac):
        self.frac = frac
        self.bar.set(frac)

    def set_state(self, state):
        self.st = state
        if state == "run":
            self.btn.configure(state="disabled")
            self.pbtn.configure(text="⏸", state="normal")
            self.pbtn.grid()
            self.bar.grid()
        elif state == "paused":
            self.btn.configure(state="normal", text="✕")
            self.pbtn.configure(text="▶", state="normal")
            self.pbtn.grid()
            if self.frac > 0:
                self.bar.grid()
        else:
            self.bar.grid_remove()
            self.btn.configure(state="normal", text="📂" if state == "done" else "✕")
            if state == "done":
                self.pbtn.grid_remove()
            else:
                self.pbtn.configure(text="▶" if state == "error" else "⏸", state="normal")
                self.pbtn.grid()

    def on_button(self):
        if self.st == "done" and self.file_path and os.path.exists(self.file_path):
            self.app.show_in_folder(self.file_path)
        elif self.st != "run":
            self.app.remove_item(self)

    def show_menu(self, event):
        exists = bool(self.file_path) and os.path.exists(self.file_path)
        done = self.st == "done"
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="▶  Открыть", state="normal" if done and exists else "disabled",
                      command=lambda: os.startfile(self.file_path))
        m.add_command(label="📂  Показать в папке",
                      state="normal" if done and exists else "disabled",
                      command=lambda: self.app.show_in_folder(self.file_path))
        m.add_separator()
        if self.st == "run":
            m.add_command(label="⏸  Пауза", command=lambda: self.app.toggle_pause(self))
        elif self.st in ("paused", "error"):
            m.add_command(label="▶  Продолжить", command=lambda: self.app.toggle_pause(self))
        elif self.st == "wait":
            m.add_command(label="⏸  Поставить на паузу",
                          command=lambda: self.app.toggle_pause(self))
        if done:
            m.add_command(label="⟳  Скачать снова", command=self.redownload)
        m.add_command(label="🔗  Копировать ссылку", command=self.copy_url)
        m.add_separator()
        m.add_command(label="Убрать из списка", state="disabled" if self.st == "run" else "normal",
                      command=lambda: self.app.remove_item(self))
        m.add_command(label="🗑  Удалить файл…", state="normal" if done and exists else "disabled",
                      command=self.delete_file)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def copy_url(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.url)
        self.app._last_clip = self.url        # чтобы перехват буфера не добавил её снова
        self.app.set_main_status("Ссылка скопирована")

    def redownload(self):
        opts = dict(self.opts)
        opts["cookies"] = self.app.cookie_args()
        self.app.add_item(self.url, opts)

    def delete_file(self):
        if delete_download(self.app, self.file_path, self.full_title or self.url):
            self.file_path = None
            self.set_status("🗑 Файл удалён в Корзину", GRAY_TEXT)
            self.btn.configure(text="✕")
            self.app.refresh_history_window()


# ================================================================ окно истории
class HistoryWindow(ctk.CTkToplevel):
    SHOW_MAX = 150          # сколько строк показывать за раз (иначе окно тормозит)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("История загрузок")
        self.geometry("760x560")
        self.minsize(560, 360)
        if os.path.isfile(ICON_ICO):
            self.after(250, lambda: self.iconbitmap(ICON_ICO))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        top.grid_columnconfigure(0, weight=1)
        self.search = ctk.CTkEntry(top, height=36, placeholder_text="🔍  Поиск по названию или ссылке…")
        self.search.grid(row=0, column=0, sticky="ew")
        self.search.bind("<KeyRelease>", lambda e: self._schedule_refresh())
        ctk.CTkButton(top, text="Очистить историю", width=140, height=36,
                      fg_color="transparent", border_width=1, text_color=C.OUTLINE_TEXT,
                      command=self.clear_all).grid(row=0, column=1, padx=(8, 0))

        self.list = ctk.CTkScrollableFrame(self, corner_radius=R(12))
        self.list.grid(row=1, column=0, sticky="nsew", padx=16)
        self.list.grid_columnconfigure(0, weight=1)
        self.info = ctk.CTkLabel(self, text="", text_color=GRAY_TEXT, anchor="w")
        self.info.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 12))
        self._job = None
        self.refresh()
        self.after(150, self.lift)
        self.after(200, self.search.focus_set)

    def _schedule_refresh(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self.refresh)

    def refresh(self):
        self._job = None
        for w in self.list.winfo_children():
            w.destroy()
        q = self.search.get().strip().lower()
        rows = [h for h in self.app.history
                if not q or q in (h.get("title") or "").lower() or q in h.get("url", "").lower()]
        for n, rec in enumerate(rows[:self.SHOW_MAX]):
            self._row(n, rec)
        if not rows:
            ctk.CTkLabel(self.list, text="Ничего не найдено" if q else "История пока пуста",
                         text_color=C.MUTED).grid(row=0, column=0, pady=30)
        more = f" (показаны первые {self.SHOW_MAX})" if len(rows) > self.SHOW_MAX else ""
        self.info.configure(text=f"Записей: {len(rows)}{more}")

    def _row(self, n, rec):
        row = ctk.CTkFrame(self.list, corner_radius=R(8), fg_color=C.CARD)
        row.grid(row=n, column=0, sticky="ew", pady=3, padx=2)
        row.grid_columnconfigure(0, weight=1)
        title = rec.get("title") or rec.get("url")
        ctk.CTkLabel(row, text=title if len(title) < 70 else title[:67] + "…", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="ew",
                                                                   padx=12, pady=(6, 0))
        path = rec.get("file")
        exists = bool(path) and os.path.exists(path)
        meta = [rec.get("date", ""), describe_opts(rec.get("opts") or {}),
                None if exists else "файл удалён или перемещён"]
        ctk.CTkLabel(row, text="   •   ".join(m for m in meta if m), anchor="w",
                     text_color=GRAY_TEXT if exists else ACCENT,
                     font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="ew",
                                                    padx=12, pady=(0, 6))
        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.grid(row=0, column=1, rowspan=2, padx=8)
        small = dict(width=34, height=30, fg_color="transparent",
                     hover_color=C.ICON_HOVER, text_color=C.OUTLINE_TEXT)
        ctk.CTkButton(btns, text="▶", state="normal" if exists else "disabled",
                      command=lambda: os.startfile(path), **small).pack(side="left")
        ctk.CTkButton(btns, text="📂", command=lambda: self.app.show_in_folder(path or ""),
                      **small).pack(side="left")
        ctk.CTkButton(btns, text="⟳ Скачать снова", width=130, height=30,
                      command=lambda: self.redownload(rec)).pack(side="left", padx=(6, 4))
        ctk.CTkButton(btns, text="✕", command=lambda: self.remove(rec), **small).pack(side="left")
        self.after(50, lambda: bind_right_click(row, lambda e: self.show_menu(e, rec)))

    def show_menu(self, event, rec):
        path = rec.get("file")
        exists = bool(path) and os.path.exists(path)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="▶  Открыть", state="normal" if exists else "disabled",
                      command=lambda: os.startfile(path))
        m.add_command(label="📂  Показать в папке",
                      command=lambda: self.app.show_in_folder(path or ""))
        m.add_command(label="⟳  Скачать снова", command=lambda: self.redownload(rec))
        m.add_command(label="🔗  Копировать ссылку", command=lambda: self.copy_url(rec))
        m.add_separator()
        m.add_command(label="Убрать из истории", command=lambda: self.remove(rec))
        m.add_command(label="🗑  Удалить файл…", state="normal" if exists else "disabled",
                      command=lambda: self.delete_file(rec))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def copy_url(self, rec):
        self.app.clipboard_clear()
        self.app.clipboard_append(rec["url"])
        self.app._last_clip = rec["url"]

    def delete_file(self, rec):
        if delete_download(self.app, rec.get("file"), rec.get("title") or rec["url"]):
            self.refresh()

    def redownload(self, rec):
        opts = dict(rec.get("opts") or {})
        opts["cookies"] = self.app.cookie_args()     # cookies берём текущие
        if self.app.add_item(rec["url"], opts):
            self.app.set_main_status("Добавлено из истории: " + (rec.get("title") or rec["url"]))

    def remove(self, rec):
        if rec in self.app.history:
            self.app.history.remove(rec)
            save_history(self.app.history)
        self.refresh()

    def clear_all(self):
        self.app.history.clear()
        save_history(self.app.history)
        self.refresh()


# ================================================================ окно «Список»
class BulkWindow(ctk.CTkToplevel):
    """Большое поле: сразу много ссылок или названий песен, по одному на строку."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Список ссылок и песен")
        self.geometry("620x520")
        self.minsize(460, 360)
        if os.path.isfile(ICON_ICO):
            self.after(250, lambda: self.iconbitmap(ICON_ICO))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, anchor="w", justify="left", text_color=GRAY_TEXT,
                     text="По одной на строку: ссылка или название песни.\n"
                          "Например: «Король и Шут — Лесник». Названия ищутся на YouTube, "
                          "берётся первый результат.").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.text = ctk.CTkTextbox(self, corner_radius=R(10), font=ctk.CTkFont(size=14))
        self.text.grid(row=1, column=0, sticky="nsew", padx=16)
        self.text.bind("<KeyRelease>", lambda e: self.update_count())
        self.text.bind("<<Paste>>", lambda e: self.after(50, self.update_count))
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        bottom.grid_columnconfigure(0, weight=1)
        self.count = ctk.CTkLabel(bottom, text="", anchor="w", text_color=GRAY_TEXT)
        self.count.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(bottom, text="В очередь", width=110,
                      command=lambda: self.submit(False)).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(bottom, text="⬇  Скачать всё", width=140, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=lambda: self.submit(True)).grid(
            row=0, column=2, padx=(8, 0))
        self.update_count()
        self.after(150, self.lift)
        self.after(200, self.text.focus_set)

    def update_count(self):
        items = parse_input(self.text.get("1.0", "end"))
        searches = sum(1 for u in items if is_search(u))
        self.count.configure(text=f"Ссылок: {len(items) - searches}   •   "
                                  f"песен для поиска: {searches}")

    def submit(self, start):
        added = self.app.add_many(self.text.get("1.0", "end"))
        if added:
            self.destroy()
            if start:
                self.app.start()


# ================================================================ окно настроек
class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Настройки")
        self.geometry("620x680")
        self.minsize(520, 420)
        if os.path.isfile(ICON_ICO):
            self.after(250, lambda: self.iconbitmap(ICON_ICO))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, corner_radius=R(0), fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        self.body = body
        self.row = 0

        # --- внешний вид и поведение
        self.section("Программа")
        line = self.line()
        ctk.CTkLabel(line, text="Оформление:").pack(side="left")
        self.style_menu = ctk.CTkOptionMenu(
            line, values=list(STYLES), width=210, command=self.change_style)
        self.style_menu.set(next(k for k, v in STYLES.items() if v == STYLE))
        self.style_menu.pack(side="left", padx=8)
        self.switch("Тёмная тема", app.v_theme, app.toggle_theme,
                    "В оформлении Phantom тема всегда тёмная" if PHANTOM else None)
        self.switch("Ловить ссылки из буфера обмена", app.v_clip, app.toggle_clip_watch)
        self.switch("Крестик сворачивает в трей", app.v_tray, app._save,
                    None if app.tray else "Значок в трее недоступен на этом компьютере")
        self.switch("Звук и уведомление, когда всё скачано", app.v_sound, app._toggle_sound)
        self.switch("Запускать вместе с Windows (свёрнутой в трей)", app.v_autostart,
                    app.toggle_autostart,
                    None if getattr(sys, "frozen", False) else "Доступно только в Downloader.exe")
        self.switch("Проверять обновления при запуске", app.v_check_updates, app._save)
        self.switch("Обновлять yt-dlp автоматически (раз в день, тихо)", app.v_auto_ytdlp,
                    app._save)

        # --- скачивание
        self.section("Скачивание")
        line = self.line()
        ctk.CTkLabel(line, text="Скачивать одновременно:").pack(side="left")
        ctk.CTkOptionMenu(line, values=PARALLEL_CHOICES, width=70, variable=app.v_parallel,
                          command=lambda _: app._save()).pack(side="left", padx=8)
        self.check("Субтитры (русские и английские) — вшивать в видео", app.v_subs)
        self.check("Обложка и теги — вшивать в файл", app.v_embed)
        self.check("Фрагмент — резать точно по секундам (медленнее)", app.v_exact)
        line = self.line()
        ctk.CTkLabel(line, text="🍪 Cookies для входа на сайты:").pack(side="left")
        ctk.CTkOptionMenu(line, values=list(COOKIE_SOURCES), width=170, variable=app.v_cookies,
                          command=app._on_cookies).pack(side="left", padx=8)
        self.hint("Нужны для видео 18+, закрытых видео, Instagram и части VK. Надёжнее всего "
                  "Firefox или файл cookies.txt (расширение «Get cookies.txt LOCALLY»).")

        # --- папки и имена файлов
        self.section("Музыка (режим «Аудио»)")
        self.check("Квадратная обложка — как у альбома, а не кадр 16:9", app.v_square)
        self.check("Выравнивать громкость — все треки звучат одинаково громко", app.v_normalize)
        self.check("Тексты песен — файл .lrc рядом с треком (Poweramp, foobar, AIMP)",
                   app.v_lyrics)
        self.hint("Тексты берутся из субтитров клипа — они есть не у всех видео. "
                  "Выравнивание громкости заново кодирует трек, это занимает несколько секунд.")

        self.section("Сеть — если YouTube открывается только с VPN")
        line = self.line()
        ctk.CTkLabel(line, text="Прокси:").pack(side="left")
        ctk.CTkOptionMenu(line, values=PROXY_MODES, width=160, variable=app.v_proxy_mode,
                          command=lambda _: app._save()).pack(side="left", padx=8)
        line = self.line()
        self.proxy_entry = ctk.CTkEntry(line, textvariable=app.v_proxy, width=250, height=32,
                                        placeholder_text="socks5://127.0.0.1:10808")
        self.proxy_entry.pack(side="left")
        self.proxy_entry.bind("<FocusOut>", lambda e: app._save())
        ctk.CTkButton(line, text="Найти", width=80, height=32,
                      command=self.find_proxy).pack(side="left", padx=(8, 0))
        ctk.CTkButton(line, text="Проверить", width=100, height=32, fg_color="transparent",
                      border_width=1, text_color=C.OUTLINE_TEXT,
                      command=self.test_proxy).pack(side="left", padx=(8, 0))
        self.proxy_result = self.hint("")
        self.hint("VPN-программы (v2rayN, Hiddify, Nekoray, Clash и др.) открывают на компьютере "
                  "локальный прокси. Через него программа будет качать с YouTube, а остальные "
                  "сайты — напрямую. VPN-программа должна быть запущена, но включать VPN для "
                  "всего компьютера не обязательно. «Найти» ищет такой прокси сама.")

        self.section("Папки и имена файлов")
        line = self.line()
        ctk.CTkLabel(line, text="Раскладывать:").pack(side="left")
        ctk.CTkOptionMenu(line, values=list(ORGANIZE), width=190, variable=app.v_org,
                          command=lambda _: self.update_layout()).pack(side="left", padx=8)
        line = self.line()
        ctk.CTkLabel(line, text="Имя файла:").pack(side="left")
        ctk.CTkOptionMenu(line, values=list(NAME_TEMPLATES), width=190, variable=app.v_name,
                          command=lambda _: self.update_layout()).pack(side="left", padx=8)
        self.custom = ctk.CTkEntry(body, textvariable=app.v_custom, height=34,
                                   font=ctk.CTkFont(family="Consolas", size=13))
        self.custom.grid(row=self.next(), column=0, sticky="ew", padx=24, pady=4)
        self.custom.bind("<KeyRelease>", lambda e: self.update_layout())
        self.custom_hint = self.hint(
            "Поля: %(title)s — название, %(uploader)s — канал, %(upload_date)s — дата, "
            "%(id)s — код видео, %(playlist_index)s — номер. Папки — через «/».")
        self.example = ctk.CTkLabel(body, text="", anchor="w", justify="left",
                                    font=ctk.CTkFont(family="Consolas", size=12),
                                    text_color=C.TEXT, wraplength=540)
        self.example.grid(row=self.next(), column=0, sticky="ew", padx=24, pady=(6, 4))
        self.hint("Настройки применяются к ссылкам, которые добавлены после изменения.")

        ctk.CTkButton(self, text="Готово", width=120, height=36, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self.destroy).grid(
            row=1, column=0, sticky="e", padx=20, pady=12)
        self.update_layout()
        self.after(150, self.lift)

    # маленькие помощники для вёрстки
    def next(self):
        self.row += 1
        return self.row

    def section(self, text):
        ctk.CTkLabel(self.body, text=text, anchor="w",
                     font=title_font(16)).grid(
            row=self.next(), column=0, sticky="ew", padx=16, pady=(16, 4))

    def switch(self, text, var, command, disabled_reason=None):
        w = ctk.CTkSwitch(self.body, text=text, variable=var, command=command)
        w.grid(row=self.next(), column=0, sticky="w", padx=24, pady=4)
        if disabled_reason:
            w.configure(state="disabled")
            self.hint(disabled_reason)

    def check(self, text, var):
        ctk.CTkCheckBox(self.body, text=text, variable=var, command=self.app._save).grid(
            row=self.next(), column=0, sticky="w", padx=24, pady=4)

    def line(self):
        f = ctk.CTkFrame(self.body, fg_color="transparent")
        f.grid(row=self.next(), column=0, sticky="w", padx=24, pady=4)
        return f

    def hint(self, text):
        lbl = ctk.CTkLabel(self.body, text=text, anchor="w", justify="left", wraplength=540,
                           text_color=GRAY_TEXT, font=ctk.CTkFont(size=12))
        lbl.grid(row=self.next(), column=0, sticky="ew", padx=24, pady=(0, 4))
        return lbl

    def change_style(self, name):
        new = STYLES[name]
        if new == STYLE:
            return
        self.app.settings["style"] = new
        self.app._save()
        if messagebox.askyesno("Оформление", "Новое оформление включится после перезапуска.\n"
                                             "Перезапустить сейчас? Очередь сохранится.",
                               parent=self):
            restart_program(self.app)

    def find_proxy(self):
        self.proxy_result.configure(text="Ищу прокси VPN-программы…", text_color=GRAY_TEXT)

        def run():
            found = detect_local_proxy()
            self.after(0, lambda: self._proxy_found(found))
        threading.Thread(target=run, daemon=True).start()

    def _proxy_found(self, found):
        if not self.winfo_exists():
            return
        if not found:
            self.proxy_result.configure(
                text="Не нашёл. Запустите VPN-программу и посмотрите в её настройках порт "
                     "«SOCKS» или «Mixed» — впишите его, например 127.0.0.1:10808.",
                text_color=ACCENT)
            return
        addr, name = found
        self.app.v_proxy.set(addr)
        if self.app.v_proxy_mode.get() == "Нет":
            self.app.v_proxy_mode.set("Только YouTube")
        self.app._save()
        self.proxy_result.configure(text=f"Найден: {addr} ({name}). Проверяю YouTube…",
                                    text_color=GRAY_TEXT)
        self.test_proxy()

    def test_proxy(self):
        self.app._save()
        proxy = self.app.settings.get("proxy")
        self.proxy_result.configure(text="Проверяю, открывается ли YouTube…", text_color=GRAY_TEXT)

        def run():
            ok, text = check_youtube(proxy or None)
            msg = ("✓ " if ok else "✕ ") + text + \
                (f" через {proxy}" if proxy else " напрямую, без прокси")
            self.after(0, lambda: self._show_proxy_result(msg, ok))
        threading.Thread(target=run, daemon=True).start()

    def _show_proxy_result(self, msg, ok):
        if self.winfo_exists():
            self.proxy_result.configure(text=msg, text_color=GREEN if ok else ACCENT)

    def update_layout(self):
        custom = self.app.v_name.get() == "Свой шаблон"
        self.custom.configure(state="normal" if custom else "disabled")
        ext = "mp3" if self.app.mode.get() == "Аудио" else "mp4"
        sample = example_path(self.app.v_org.get(), self.app.v_name.get(),
                              self.app.v_custom.get(), ext)
        self.example.configure(text=f"Пример:  {self.app.settings['folder']}\\{sample}")
        self.app._save()
        self.app.refresh_folder_label()


# ================================================================ главное окно
class App(ctk.CTk):
    def __init__(self):
        self.settings = load_settings()
        init_style()
        ctk.set_appearance_mode("dark" if PHANTOM else self.settings["theme"])
        ctk.set_default_color_theme("blue")
        if PHANTOM:
            phantom_style.apply_theme(ctk.ThemeManager.theme)
        super().__init__()

        build = f" (сборка {BUILD_ID})" if BUILD_ID else ""
        self.title(f"Downloader {APP_VERSION}{build}")
        self.geometry("880x800")
        self.minsize(840, 640)
        if os.path.isfile(ICON_ICO):
            try:
                self.iconbitmap(ICON_ICO)
            except tk.TclError:
                pass

        self.items = []
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.procs = set()
        self.item_procs = {}
        self.workers_alive = 0
        self.batch_ok = self.batch_fail = 0
        self.cancel_flag = threading.Event()
        self.preview_slots = threading.Semaphore(3)   # не больше 3 превью одновременно
        self.history = load_history()
        self.history_win = None
        self.tray = None
        self.dnd_ready = False
        self._tray_hint_shown = False
        self._last_clip = self._read_clipboard()

        self.settings_win = None
        self._create_vars()
        self._build_ui()
        self._apply_mode()
        self._setup_clipboard_keys()
        self._setup_dnd()
        self._setup_tray()
        self.after(100, self._poll_events)
        self.after(300, self._autopaste)
        self.after(700, self._watch_clipboard)
        self._tools_busy = False
        self.after(8000, self._auto_ytdlp_tick)
        if self.settings["check_updates"] and getattr(sys, "frozen", False):
            self.after(2000, lambda: self.check_updates(manual=False))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log(f"Downloader {APP_VERSION}{build}")
        self.log(f"Папка программы: {BASE_DIR}")
        self.log(f"yt-dlp: {YTDLP if os.path.isfile(YTDLP) else 'НЕ НАЙДЕН'}")
        self.log(f"ffmpeg: {FFMPEG or 'НЕ НАЙДЕН (MP3 и склейка видео работать не будут)'}")
        self.log(f"deno:   {DENO or 'НЕ НАЙДЕН (YouTube может отдавать не все форматы)'}")
        if not NO_SAVE:
            self._check_tools()
        self.refresh_folder_label()
        if not NO_SAVE:
            self.restore_queue()
            if self.v_autostart.get():
                set_autostart(True)          # обновить путь, если папку с программой перенесли
        if "--tray" in sys.argv:             # запуск вместе с Windows — сразу в трей
            self.after(300, self.withdraw if self.tray else self.iconify)

    # ------------------------------------------------------------ интерфейс
    def _create_vars(self):
        """Все переключатели — в переменных: ими пользуются и главное окно, и окно настроек."""
        s = self.settings
        B, S = ctk.BooleanVar, ctk.StringVar
        self.v_theme = B(value=s["theme"] == "dark")
        self.v_clip = B(value=s["clip_watch"])
        self.v_tray = B(value=s["tray"])
        self.v_sound = B(value=s["sound"])
        self.v_autostart = B(value=s["autostart"])
        self.v_check_updates = B(value=s["check_updates"])
        self.v_auto_ytdlp = B(value=s["auto_ytdlp"])
        self.v_parallel = S(value=str(s["parallel"]))
        self.v_playlist = B(value=s["playlist"])
        self.v_subs = B(value=s["subs"])
        self.v_embed = B(value=s["embed"])
        self.v_sponsor = B(value=s["sponsorblock"])
        self.v_chapters = B(value=s["chapters"])
        self.v_exact = B(value=s["exact_cut"])
        self.v_square = B(value=s["square_cover"])
        self.v_normalize = B(value=s["normalize"])
        self.v_lyrics = B(value=s["lyrics"])
        self.v_proxy_mode = S(value=s["proxy_mode"] if s["proxy_mode"] in PROXY_MODES else "Нет")
        self.v_proxy = S(value=s["proxy"])
        self.v_cookies = S(value=s["cookies"] if s["cookies"] in COOKIE_SOURCES else "Нет")
        self.v_org = S(value=s["organize"] if s["organize"] in ORGANIZE else "Не раскладывать")
        self.v_name = S(value=s["name_template"] if s["name_template"] in NAME_TEMPLATES
                        else "Название")
        self.v_custom = S(value=s["custom_template"])

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)
        pad = {"padx": 20}
        secondary = dict(fg_color=C.BTN2, hover_color=C.BTN2_HOVER,
                         text_color=C.BTN2_TEXT)
        outline = dict(fg_color="transparent", border_width=1,
                       text_color=C.OUTLINE_TEXT)

        # --- шапка
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(16, 6), **pad)
        head.grid_columnconfigure(1, weight=1)
        version = f"v{APP_VERSION}" + (f"  ·  сборка {BUILD_ID}" if BUILD_ID else "  ·  локальная сборка")
        if PHANTOM:
            banner = phantom_style.make_banner(
                subtitle=f"ВИДЕО · МУЗЫКА · 1000+ САЙТОВ     v{APP_VERSION}", logo_path=ICON_PNG)
            self.header_img = ctk.CTkImage(light_image=banner, dark_image=banner,
                                           size=(banner.width // 2, banner.height // 2))
            ctk.CTkLabel(head, text="", image=self.header_img).grid(
                row=0, column=0, columnspan=2, rowspan=2, sticky="w")
        elif os.path.isfile(ICON_PNG):
            logo_img = Image.open(ICON_PNG)
            self.logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(52, 52))
            ctk.CTkLabel(head, text="", image=self.logo).grid(row=0, column=0, rowspan=2,
                                                              padx=(0, 12))
        if not PHANTOM:
            ctk.CTkLabel(head, text="Downloader", font=ctk.CTkFont(size=26, weight="bold")).grid(
                row=0, column=1, sticky="sw")
            ctk.CTkLabel(head, text=f"YouTube, VK, TikTok и ещё 1000+ сайтов   ·   {version}",
                         text_color=GRAY_TEXT).grid(row=1, column=1, sticky="nw")
        ctk.CTkButton(head, text=caps("⚙  Настройки"), width=130, height=38, corner_radius=R(10),
                      command=self.open_settings, **outline).grid(row=0, column=2, rowspan=2,
                                                                  sticky="e")

        # --- плашка «вышла новая версия» (скрыта, пока не нужна)
        self.banner = ctk.CTkFrame(self, corner_radius=R(10), fg_color=C.BANNER,
                                   border_width=1, border_color=ACCENT)
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_lbl = ctk.CTkLabel(self.banner, text="", anchor="w", justify="left",
                                       text_color=C.TEXT)
        self.banner_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        self.banner_btn = ctk.CTkButton(self.banner, text="Обновить", width=110,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.banner_btn.grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(self.banner, text="✕", width=30, fg_color="transparent",
                      hover_color=C.ICON_HOVER, text_color=C.ICON_TEXT,
                      command=self.hide_banner).grid(row=0, column=2, padx=(0, 8))

        # --- ссылка
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.grid(row=2, column=0, sticky="ew", pady=8, **pad)
        url_row.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(url_row, height=42, corner_radius=R(10),
                                      placeholder_text="Ссылка или название песни — и Enter",
                                      font=ctk.CTkFont(size=14))
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda e: self.add_from_entry())
        ctk.CTkButton(url_row, text="Вставить", width=90, height=42, corner_radius=R(10),
                      command=self.paste_clipboard, **secondary).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(url_row, text="📋 Список", width=100, height=42, corner_radius=R(10),
                      command=self.open_bulk, **secondary).grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(url_row, text="+ В очередь", width=110, height=42, corner_radius=R(10),
                      command=self.add_from_entry).grid(row=0, column=3, padx=(8, 0))

        # --- настройки
        card = ctk.CTkFrame(self, corner_radius=R(14))
        card.grid(row=3, column=0, sticky="ew", pady=8, **pad)
        card.grid_columnconfigure(5, weight=1)

        self.mode = ctk.CTkSegmentedButton(card, values=["Видео", "Аудио"], height=34,
                                           command=lambda _: self._apply_mode())
        self.mode.set(self.settings["mode"])
        self.mode.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="w")

        ctk.CTkLabel(card, text="Качество:").grid(row=0, column=1, padx=(10, 6), pady=(14, 8))
        self.quality = ctk.CTkOptionMenu(card, values=list(VIDEO_QUALITIES), width=120,
                                         command=lambda _: self._remember_quality())
        self.quality.grid(row=0, column=2, pady=(14, 8), sticky="w")

        ctk.CTkLabel(card, text="Формат:").grid(row=0, column=3, padx=(14, 6), pady=(14, 8))
        self.fmt = ctk.CTkOptionMenu(card, values=list(VIDEO_FORMATS), width=90,
                                     command=lambda _: self._remember_format())
        self.fmt.grid(row=0, column=4, pady=(14, 8), sticky="w")

        flags = ctk.CTkFrame(card, fg_color="transparent")
        flags.grid(row=1, column=0, columnspan=6, sticky="w", padx=14, pady=(0, 10))
        ctk.CTkCheckBox(flags, text="Весь плейлист", variable=self.v_playlist,
                        command=self._save).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(flags, text="Без рекламы", variable=self.v_sponsor,
                        command=self._toggle_sponsor).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(flags, text="Разбить по главам", variable=self.v_chapters,
                        command=self._toggle_chapters).pack(side="left")

        # фрагмент
        extra = ctk.CTkFrame(card, fg_color="transparent")
        extra.grid(row=2, column=0, columnspan=6, sticky="w", padx=14, pady=(0, 10))
        ctk.CTkLabel(extra, text="✂  Фрагмент с").grid(row=0, column=0, padx=(0, 6))
        self.cut_from = ctk.CTkEntry(extra, width=70, placeholder_text="0:00")
        self.cut_from.grid(row=0, column=1)
        ctk.CTkLabel(extra, text="до").grid(row=0, column=2, padx=6)
        self.cut_to = ctk.CTkEntry(extra, width=70, placeholder_text="конец")
        self.cut_to.grid(row=0, column=3)
        ctk.CTkButton(extra, text="✕", width=28, height=28, fg_color="transparent",
                      hover_color=C.ICON_HOVER, text_color=C.ICON_TEXT,
                      command=self.clear_cut).grid(row=0, column=4, padx=(4, 8))
        ctk.CTkLabel(extra, text="например 1:20 — 3:45", text_color=GRAY_TEXT).grid(
            row=0, column=5)

        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=3, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 14))
        folder_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(folder_row, text="📁").grid(row=0, column=0, padx=(0, 6))
        self.folder_label = ctk.CTkLabel(folder_row, text="", anchor="w",
                                         text_color=C.FOLDER_TEXT)
        self.folder_label.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(folder_row, text="Изменить", width=90,
                      command=self.choose_folder).grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(folder_row, text="Открыть", width=90, command=self.open_folder,
                      **secondary).grid(row=0, column=3, padx=(8, 0))

        # --- очередь
        qhead = ctk.CTkFrame(self, fg_color="transparent")
        qhead.grid(row=4, column=0, sticky="ew", pady=(8, 0), **pad)
        qhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(qhead, text=caps("Очередь"),
                     font=title_font(15)).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(qhead, text="🕘 История", width=100, height=26,
                      command=self.open_history, **outline).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(qhead, text="Убрать готовые", width=120, height=26,
                      command=self.clear_finished, **outline).grid(row=0, column=2)

        self.queue_frame = ctk.CTkScrollableFrame(self, corner_radius=R(14))
        self.queue_frame.grid(row=5, column=0, sticky="nsew", pady=6, **pad)
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.queue_frame,
                                        text=(caps("Очередь пуста") + "\n") * PHANTOM +
                                             "Здесь появятся ссылки для скачивания\n"
                                             "Можно перетащить ссылку из браузера прямо в окно",
                                        text_color=C.MUTED)
        self.empty_label.grid(row=0, column=0, pady=30)

        # --- общий прогресс
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=6, column=0, sticky="ew", pady=(4, 0), **pad)
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog, height=10, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status = ctk.CTkLabel(prog, text="Готов к работе", anchor="w",
                                   text_color=GRAY_TEXT)
        self.status.grid(row=1, column=0, sticky="ew")

        # --- кнопки
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=7, column=0, sticky="ew", pady=(4, 8), **pad)
        btns.grid_columnconfigure(3, weight=1)
        self.start_btn = ctk.CTkButton(btns, text=caps("⬇  Скачать"), width=150, height=42,
                                       corner_radius=R(10), fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER,
                                       font=title_font(15),
                                       command=self.start)
        self.start_btn.grid(row=0, column=0)
        self.cancel_btn = ctk.CTkButton(btns, text="⏸  Пауза", width=100, height=42,
                                        corner_radius=R(10), state="disabled",
                                        command=self.cancel, **secondary)
        self.cancel_btn.grid(row=0, column=1, padx=8)
        self.log_btn = ctk.CTkButton(btns, text="Журнал ▾", width=100, height=42,
                                     corner_radius=R(10), command=self.toggle_log, **outline)
        self.log_btn.grid(row=0, column=2)
        self.update_btn = ctk.CTkButton(btns, text="Обновить компоненты", width=170, height=42,
                                        corner_radius=R(10), command=self.update_ytdlp, **outline)
        self.update_btn.grid(row=0, column=4, padx=(0, 8))
        self.app_update_btn = ctk.CTkButton(btns, text="Проверить обновления", width=170,
                                            height=42, corner_radius=R(10),
                                            command=lambda: self.check_updates(manual=True),
                                            **outline)
        self.app_update_btn.grid(row=0, column=5)

        # --- журнал (скрыт по умолчанию)
        self.log_box = ctk.CTkTextbox(self, height=130, corner_radius=R(10),
                                      font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.configure(state="disabled")
        self.log_visible = False

    def set_main_status(self, text, color=None):
        self.status.configure(text=text, text_color=color or GRAY_TEXT)

    def show_banner(self, text, button_text=None, command=None):
        self.banner_lbl.configure(text=text)
        if button_text:
            self.banner_btn.configure(text=button_text, command=command)
            self.banner_btn.grid()
        else:
            self.banner_btn.grid_remove()
        self.banner.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))

    def hide_banner(self):
        self.banner.grid_remove()

    # ------------------------------------------------------------ Ctrl+V и меню
    def _setup_clipboard_keys(self):
        # Tkinter не понимает Ctrl+V/C/X/A при русской раскладке — ловим по коду клавиши
        self.bind_all("<Control-KeyPress>", self._on_ctrl_key, add="+")

        # меню по правой кнопке мыши в поле ссылки
        self.entry_menu = tk.Menu(self, tearoff=0)
        self.entry_menu.add_command(label="Вставить", command=lambda: self._entry_do("<<Paste>>"))
        self.entry_menu.add_command(label="Вставить и добавить в очередь",
                                    command=self._paste_and_add)
        self.entry_menu.add_separator()
        self.entry_menu.add_command(label="Копировать", command=lambda: self._entry_do("<<Copy>>"))
        self.entry_menu.add_command(label="Вырезать", command=lambda: self._entry_do("<<Cut>>"))
        self.entry_menu.add_command(label="Выделить всё",
                                    command=lambda: self._entry_do("<<SelectAll>>"))
        self.entry_menu.add_command(label="Очистить",
                                    command=lambda: self.url_entry.delete(0, "end"))
        self.url_entry.bind("<Button-3>", self._show_entry_menu)

    def _on_ctrl_key(self, event):
        action = CTRL_KEYCODES.get(event.keycode)
        if not action:
            return None
        # на английской раскладке Tk сам обрабатывает Ctrl+V/C/X — не дублируем
        if event.keysym.lower() in ("v", "c", "x"):
            return None
        widget = event.widget
        if not hasattr(widget, "event_generate"):
            return None
        widget.event_generate(action)
        return "break"

    def _entry_do(self, action):
        self.url_entry.focus_set()
        self.url_entry._entry.event_generate(action)

    def _paste_and_add(self):
        self.paste_clipboard()
        self.add_from_entry()

    def _show_entry_menu(self, event):
        try:
            self.entry_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.entry_menu.grab_release()

    # ------------------------------------------------------------ буфер обмена
    def _read_clipboard(self):
        try:
            return self.clipboard_get().strip()
        except Exception:
            return ""

    def toggle_clip_watch(self):
        # при включении не хватаем то, что уже лежало в буфере
        self._last_clip = self._read_clipboard()
        self.settings["clip_watch"] = self.v_clip.get()
        self._save()
        if self.v_clip.get():
            self.set_main_status("Скопируйте ссылку в браузере — она сама появится в очереди")

    def _watch_clipboard(self):
        """Раз в 0.7 с проверяет буфер обмена и добавляет новые ссылки в очередь."""
        if self.v_clip.get():
            text = self._read_clipboard()
            if text and text != self._last_clip:
                self._last_clip = text
                known = {i.url for i in self.items}
                urls = [u for u in text.split() if URL_RE.match(u) and u not in known]
                urls = [u for u in urls if self.add_item(u)]
                if urls:
                    self.set_main_status(f"Добавлено из буфера: {len(urls)}")
                    if self.state() == "iconic":
                        self.bell()
        self.after(700, self._watch_clipboard)

    # ------------------------------------------------------------ настройки
    def _apply_mode(self):
        if self.mode.get() == "Аудио":
            self.quality.configure(values=list(AUDIO_QUALITIES))
            self.quality.set(self.settings["audio_quality"])
            self.fmt.configure(values=list(AUDIO_FORMATS))
            self.fmt.set(self.settings["audio_format"])
        else:
            self.quality.configure(values=list(VIDEO_QUALITIES))
            self.quality.set(self.settings["video_quality"])
            self.fmt.configure(values=list(VIDEO_FORMATS))
            self.fmt.set(self.settings["video_format"])
        self._update_quality_state()
        self._save()

    def _remember_format(self):
        key = "audio_format" if self.mode.get() == "Аудио" else "video_format"
        self.settings[key] = self.fmt.get()
        self._update_quality_state()
        self._save()

    def _update_quality_state(self):
        # у FLAC и WAV нет битрейта — качество всегда максимальное
        lossless = self.mode.get() == "Аудио" and AUDIO_FORMATS[self.fmt.get()][1]
        self.quality.configure(state="disabled" if lossless else "normal")

    def clear_cut(self):
        for entry, hint in ((self.cut_from, "0:00"), (self.cut_to, "конец")):
            entry.delete(0, "end")
            entry.configure(placeholder_text=hint)
        self.focus_set()

    def _on_cookies(self, choice):
        if COOKIE_SOURCES[choice] == "file":
            path = filedialog.askopenfilename(
                title="Файл cookies.txt (формат Netscape)",
                filetypes=[("cookies.txt", "*.txt"), ("Все файлы", "*.*")])
            if not path:
                self.v_cookies.set(self.settings["cookies"])
                return
            self.settings["cookies_file"] = os.path.normpath(path)
            self.set_main_status(f"Cookies из файла: {os.path.basename(path)}")
        elif choice in ("Chrome", "Edge", "Яндекс"):
            self.set_main_status(f"{choice}: если cookies не возьмутся — закройте браузер "
                                 "полностью или используйте Firefox / файл cookies.txt")
        elif choice != "Нет":
            self.set_main_status(f"Cookies берутся из {choice}")
        self.settings["cookies"] = choice
        self._save()

    def cookie_args(self):
        src = COOKIE_SOURCES.get(self.v_cookies.get())
        if not src:
            return None
        if src == "file":
            path = self.settings.get("cookies_file")
            return ["--cookies", path] if path and os.path.isfile(path) else None
        return ["--cookies-from-browser", src]

    def _remember_quality(self):
        key = "audio_quality" if self.mode.get() == "Аудио" else "video_quality"
        self.settings[key] = self.quality.get()
        self._save()

    def _save(self):
        self.settings.update(mode=self.mode.get(), playlist=self.v_playlist.get(),
                             subs=self.v_subs.get(), embed=self.v_embed.get(),
                             parallel=self.v_parallel.get(), clip_watch=self.v_clip.get(),
                             autostart=self.v_autostart.get(),
                             check_updates=self.v_check_updates.get(),
                             auto_ytdlp=self.v_auto_ytdlp.get(),
                             organize=self.v_org.get(), name_template=self.v_name.get(),
                             custom_template=self.v_custom.get(),
                             exact_cut=self.v_exact.get(), tray=self.v_tray.get(),
                             sound=self.v_sound.get(), sponsorblock=self.v_sponsor.get(),
                             chapters=self.v_chapters.get(),
                             square_cover=self.v_square.get(),
                             normalize=self.v_normalize.get(), lyrics=self.v_lyrics.get(),
                             proxy_mode=self.v_proxy_mode.get(),
                             proxy=normalize_proxy(self.v_proxy.get()))
        save_settings(self.settings)

    def current_opts(self):
        """Настройки для новой ссылки. Неверное время фрагмента -> ValueError."""
        start = parse_time(self.cut_from.get())
        end = parse_time(self.cut_to.get())
        if start is not None and end is not None and end <= start:
            raise ValueError("конец раньше начала")
        cut = (start, end) if (start or end) else None
        return {"mode": self.mode.get(), "quality": self.quality.get(),
                "fmt": self.fmt.get(),
                "playlist": self.v_playlist.get(),
                "subs": self.v_subs.get() and self.mode.get() == "Видео",
                "embed": self.v_embed.get(),
                "sponsor": self.v_sponsor.get(),
                "chapters": self.v_chapters.get(),
                "square": self.v_square.get(),
                "normalize": self.v_normalize.get(),
                "lyrics": self.v_lyrics.get(),
                "cut": cut, "exact": self.v_exact.get(),
                "layout": {"org": self.v_org.get(), "name": self.v_name.get(),
                           "custom": self.v_custom.get()},
                "cookies": self.cookie_args()}

    def toggle_theme(self):
        theme = "dark" if self.v_theme.get() else "light"
        ctk.set_appearance_mode(theme)
        self.settings["theme"] = theme
        self._save()

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.settings["folder"],
                                       title="Папка для загрузок")
        if path:
            self.settings["folder"] = os.path.normpath(path)
            self.refresh_folder_label()
            self._save()

    def open_folder(self):
        os.makedirs(self.settings["folder"], exist_ok=True)
        os.startfile(self.settings["folder"])

    def show_in_folder(self, path):
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            self.open_folder()

    # ------------------------------------------------------------ ссылки и очередь
    def _autopaste(self):
        """При запуске подставляет ссылку из буфера обмена, если она там есть."""
        if self.v_clip.get():
            return
        text = self._read_clipboard()
        if text.startswith("http") and not self.url_entry.get():
            self.url_entry.insert(0, text)

    def paste_clipboard(self):
        text = self._read_clipboard()
        if text:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text)

    def add_from_entry(self):
        added = self.add_many(self.url_entry.get())
        if added:
            self.url_entry.delete(0, "end")
            self._last_clip = self._read_clipboard()   # чтобы перехват не добавил её ещё раз
        return added

    def add_many(self, text, allow_search=True):
        """Добавляет ссылки и названия из текста. Возвращает, сколько добавлено."""
        items = parse_input(text, allow_search)
        added = 0
        for url in items:
            if not self.add_item(url):
                break
            added += 1
        if added > 1:
            searches = sum(1 for u in items[:added] if is_search(u))
            self.set_main_status(f"Добавлено: {added}" +
                                 (f" (из них поиском по названию: {searches})" if searches else ""))
        return added

    def open_bulk(self):
        if getattr(self, "bulk_win", None) and self.bulk_win.winfo_exists():
            self.bulk_win.deiconify()
            self.bulk_win.lift()
            return
        self.bulk_win = BulkWindow(self)

    def add_item(self, url, opts=None):
        try:
            opts = opts or self.current_opts()
        except ValueError:
            self.set_main_status("⚠ Неверное время фрагмента. Пишите так: 1:20 или 0:01:20 "
                                 "или 80 (секунды)", ACCENT)
            return False
        self.empty_label.grid_remove()
        item = QueueItem(self.queue_frame, self, url, opts)
        item.batch = self.workers_alive > 0
        item.grid(row=len(self.items) + 1, column=0, sticky="ew", pady=4, padx=4)
        with self.lock:
            self.items.append(item)
        if self.dnd_ready:
            self.after(50, lambda: self._register_drop_tree(item))
        threading.Thread(target=self._fetch_preview, args=(item,), daemon=True).start()
        # если загрузка уже идёт, а свободные потоки закончились — запускаем ещё один
        if self.workers_alive and self.workers_alive < int(self.v_parallel.get()):
            self._spawn_worker()
        self.save_queue()
        return item

    def remove_item(self, item):
        with self.lock:
            if item.st == "run":
                return
            self.items.remove(item)
        item.destroy()
        self._regrid()
        self.save_queue()

    def clear_finished(self):
        with self.lock:
            gone = [i for i in self.items if i.st in ("done", "error", "cancel")]
            for item in gone:
                self.items.remove(item)
        for item in gone:
            item.destroy()
        self._regrid()
        self.save_queue()

    def _regrid(self):
        for n, item in enumerate(self.items):
            item.grid(row=n + 1)
        if not self.items:
            self.empty_label.grid()

    # ------------------------------------------------------------ превью
    def _fetch_preview(self, item):
        """Фоновый поток: узнаёт название, длительность, размер и обложку."""
        with self.preview_slots:
            if item not in self.items:
                return
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            try:
                opts = dict(item.opts, proxy_url=self.proxy_for(item.url))
                p = subprocess.run(preview_command(item.url, opts), capture_output=True,
                                   cwd=BASE_DIR, env=env, timeout=90,
                                   creationflags=NO_WINDOW)
                info = json.loads(p.stdout.decode("utf-8", "replace") or "null")
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                info, p = None, None
                self.events.put(("log", f"Превью {item.url}: {e}"))
            if not info:
                err = ""
                if p is not None:
                    lines = [ln for ln in ANSI.sub("", p.stderr.decode("utf-8", "replace"))
                             .splitlines() if ln.startswith("ERROR")]
                    err = lines[-1].replace("ERROR:", "").strip() if lines else ""
                if err:
                    self.events.put(("log", f"Превью {item.url}: {err}"))
                self.events.put(("meta", item, None, "Не удалось получить информацию"
                                 + (f": {err[:60]}" if err else ""), ACCENT))
                return
            if is_search(item.url):
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    self.events.put(("meta", item, None, "Ничего не найдено по этому запросу",
                                     ACCENT))
                    return
                info = entries[0]
                real = info.get("webpage_url") or \
                    f"https://www.youtube.com/watch?v={info.get('id')}"
                self.events.put(("resolved", item, real))
            s = summarize(info, item.opts)
            self.events.put(("meta", item, s["title"], s["meta"] or "—", None))
        if s.get("thumb"):
            try:
                self.events.put(("thumb", item, load_thumbnail(s["thumb"],
                                                                self.proxy_for(s["thumb"]))))
            except Exception as e:
                self.events.put(("log", f"Обложка {item.url}: {e}"))

    # ------------------------------------------------------------ журнал
    def toggle_log(self):
        if self.log_visible:
            self.log_box.grid_remove()
            self.log_btn.configure(text="Журнал ▾")
        else:
            self.log_box.grid(row=8, column=0, sticky="ew", padx=20, pady=(0, 16))
            self.log_btn.configure(text="Журнал ▴")
        self.log_visible = not self.log_visible

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------ скачивание
    def start(self):
        self.add_from_entry()
        if self.workers_alive:
            return
        if self._tools_busy:                 # как раз обновляется yt-dlp — подождём
            self.set_main_status("Обновляется yt-dlp — загрузка начнётся через пару секунд…")
            self.after(1500, self.start)
            return
        for item in self.items:
            # повторить неудачные и продолжить остановленные кнопкой «Пауза»
            # (поставленные на паузу кнопкой ⏸ у самой ссылки — не трогаем)
            if item.st == "error" or (item.st == "paused" and not item.user_paused):
                if item.st == "error":
                    item.set_progress(0)
                item.set_state("wait")
                item.set_status("В очереди")
        waiting = [i for i in self.items if i.st == "wait"]
        if not waiting:
            self.set_main_status("Добавьте хотя бы одну ссылку")
            return
        for item in self.items:
            item.batch = item.st == "wait"
        os.makedirs(self.settings["folder"], exist_ok=True)
        self.cancel_flag.clear()
        self.batch_ok = self.batch_fail = 0
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.update_btn.configure(state="disabled")
        self.progress.set(0)
        for _ in range(min(int(self.v_parallel.get()), len(waiting))):
            self._spawn_worker()

    def _spawn_worker(self):
        self.workers_alive += 1
        threading.Thread(target=self._work, daemon=True).start()

    def cancel(self):
        """Кнопка «Пауза»: останавливает все загрузки, их можно продолжить кнопкой «Скачать»."""
        self.cancel_flag.set()
        with self.lock:
            procs = list(self.procs)
            for item in self.item_procs:
                item.pause_requested = True
        for p in procs:
            kill_tree(p)

    def toggle_pause(self, item):
        """Кнопка ⏸/▶ у одной ссылки."""
        if item.st == "run":
            item.user_paused = True
            item.pause_requested = True
            item.set_status("Ставлю на паузу…")
            with self.lock:
                proc = self.item_procs.get(item)
            kill_tree(proc)
        elif item.st == "wait":
            item.user_paused = True
            item.set_state("paused")
            item.set_status("⏸ На паузе")
        elif item.st in ("paused", "error"):
            item.user_paused = False
            if item.st == "error":
                item.set_progress(0)
            item.set_state("wait")
            item.set_status("В очереди")
            if not self.workers_alive:
                self.start()
            elif self.workers_alive < int(self.v_parallel.get()):
                item.batch = True
                self._spawn_worker()
        self.save_queue()

    # ------------------------------------------------------------ очередь между запусками
    def save_queue(self):
        if NO_SAVE:
            return
        data = [{"url": i.url, "title": i.full_title, "frac": i.frac,
                 "user_paused": i.user_paused,
                 "opts": {k: v for k, v in i.opts.items() if k != "cookies"}}
                for i in self.items if i.st != "done"]
        try:
            if data:
                with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
            elif os.path.exists(QUEUE_FILE):
                os.remove(QUEUE_FILE)
        except OSError:
            pass

    def restore_queue(self):
        """Возвращает недокачанное с прошлого запуска — на паузе, продолжить кнопкой «Скачать»."""
        restored = 0
        for rec in load_queue():
            if not rec.get("url") or not isinstance(rec.get("opts"), dict):
                continue
            opts = dict(rec["opts"])
            opts["cookies"] = self.cookie_args()
            item = self.add_item(rec["url"], opts)
            if not item:
                continue
            item.set_title(rec.get("title"))
            item.set_progress(float(rec.get("frac") or 0))
            item.user_paused = bool(rec.get("user_paused"))
            item.set_state("paused")
            item.set_status("⏸ На паузе — нажмите ▶ или «Скачать», чтобы продолжить")
            restored += 1
        if restored:
            self.set_main_status(f"Недокачанное с прошлого раза: {restored} — "
                                 "нажмите «Скачать», чтобы продолжить")

    def _take_item(self):
        with self.lock:
            for item in self.items:
                if item.st == "wait":
                    item.st = "run"          # сразу занимаем, чтобы другой поток не взял
                    return item
        return None

    def _work(self):
        """Фоновый поток: берёт ссылки из очереди, пока они есть."""
        while not self.cancel_flag.is_set():
            item = self._take_item()
            if item is None:
                break
            self.events.put(("result", self._download(item)))
        self.events.put(("worker_done",))

    def _download(self, item):
        ev = self.events.put
        ev(("state", item, "run"))
        ev(("item_status", item, "Подготовка…", None))
        proxy = self.proxy_for(item.url)
        cmd = build_command(item.url, dict(item.opts, proxy_url=proxy), self.settings["folder"],
                            self.settings.get("fragments", 8))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=BASE_DIR, env=env,
                creationflags=NO_WINDOW)
        except OSError as e:
            ev(("log", f"Не удалось запустить {YTDLP}: {e}"))
            ev(("state", item, "error"))
            ev(("item_status", item, "Ошибка запуска: " + launch_error_text(e), ACCENT))
            return False
        with self.lock:
            self.procs.add(proc)
            self.item_procs[item] = proc

        last_error = ""
        files = []
        for raw in proc.stdout:
            line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
            if not line:
                continue
            if line.startswith("[P]"):
                pct, speed, eta = (line[3:].split("|") + ["", ""])[:3]
                try:
                    frac = float(pct.strip().rstrip("%")) / 100
                except ValueError:
                    frac = 0.0
                if frac >= 1:
                    text = "Обработка (ffmpeg)…"
                else:
                    text = f"{pct.strip()}   •   {speed.strip()}   •   осталось {eta.strip()}"
                ev(("item_progress", item, frac, text))
            elif line.startswith("[T]"):
                ev(("title", item, line[3:]))
            elif line.startswith("[F]"):
                item.file_path = line[3:]
                files.append(line[3:])
                ev(("log", "✓ " + line[3:]))
            else:
                ev(("log", line))
                if line.startswith("ERROR"):
                    last_error = line.replace("ERROR:", "").strip()
        code = proc.wait()
        with self.lock:
            self.procs.discard(proc)
            self.item_procs.pop(item, None)

        if item.pause_requested or self.cancel_flag.is_set():
            # недокачанный файл (.part) остаётся — yt-dlp продолжит с того же места
            item.pause_requested = False
            ev(("state", item, "paused"))
            ev(("item_status", item, f"⏸ На паузе — {int(item.frac * 100)}%", None))
            return None
        if code == 0:
            targets = list(files)
            if item.opts.get("chapters"):
                folder = finalize_chapters(files)
                if folder:
                    item.file_path = folder
                    targets = [folder]
                    ev(("log", "✓ Разбито по главам: " + folder))
            for note in postprocess_audio(
                    targets, item.opts,
                    lambda text: ev(("item_progress", item, 1.0, text))):
                ev(("log", f"♪ {item.full_title or item.url}: {note}"))
            ev(("history", item))
            ev(("state", item, "done"))
            ev(("item_status", item, "✓ Готово", GREEN))
            return True
        ev(("state", item, "error"))
        ev(("item_status", item, explain_error(last_error, code, item.url, proxy), ACCENT))
        return False

    def update_ytdlp(self):
        """Обновляет yt-dlp и deno на месте, недостающее докачивает в папку программы.
        ffmpeg обновлять не нужно — если он есть, его не трогаем."""
        if self.workers_alive or getattr(self, "_tools_busy", False):
            return
        self._tools_busy = True
        self.update_btn.configure(state="disabled")
        self.hide_banner()
        self.set_main_status("Обновляю компоненты…")
        if not self.log_visible:
            self.toggle_log()

        def run():
            ev = self.events.put
            results = []

            def progress(name):
                last = [0]

                def cb(done, total):
                    pct = int(done * 100 / total) if total else 0
                    if pct != last[0]:
                        last[0] = pct
                        ev(("tools_progress", done / total if total else 0,
                            f"Скачиваю {name}: {pct}%   ({human_size(done) or '0 Б'} из {human_size(total) or '?'})"))
                return cb

            def run_tool(cmd):
                try:
                    p = subprocess.run(cmd, capture_output=True, cwd=BASE_DIR, timeout=600,
                                       creationflags=NO_WINDOW)
                    out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", "replace"))
                except (OSError, subprocess.TimeoutExpired) as e:
                    out = str(e)
                for line in out.splitlines():
                    if line.strip():
                        ev(("log", line))
                return out

            # yt-dlp: обновляется сам
            if os.path.isfile(YTDLP):
                ev(("tools_progress", 0, "Обновляю yt-dlp…"))
                out = run_tool([YTDLP, "-U"])
                ver = re.search(r"(stable|nightly|master)@[\d.]+", out)
                ver = ver.group(0) if ver else ""
                if "up to date" in out:
                    results.append(f"yt-dlp — последняя версия {ver}".strip())
                elif "Updated yt-dlp" in out:
                    results.append(f"yt-dlp обновлён до {ver}".strip())
                else:
                    results.append("yt-dlp — не удалось обновить (см. журнал)")
            # deno: тоже умеет обновляться сам
            if DENO:
                ev(("tools_progress", 0, "Обновляю deno…"))
                out = run_tool([DENO, "upgrade"])
                ver = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
                if "most recent" in out or "already" in out.lower():
                    results.append("deno — последняя версия")
                elif "Upgraded successfully" in out or "upgraded" in out.lower():
                    results.append(f"deno обновлён{(' до ' + ver.group(1)) if ver else ''}")
                else:
                    results.append("deno — не удалось обновить (см. журнал)")
            if FFMPEG:
                results.append("ffmpeg на месте")
            # чего нет — докачиваем в папку программы
            for name in missing_tools():
                try:
                    install_tool(name, progress(name))
                    results.append(f"{name} скачан")
                    ev(("log", f"✓ {name} скачан в {BASE_DIR}"))
                except Exception as e:
                    results.append(f"{name} — не удалось скачать")
                    ev(("log", f"Не удалось скачать {name}: {e}"))
            refresh_tools()
            ev(("updated", "✓ " + "   •   ".join(results)))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ обновление программы
    def check_updates(self, manual=False):
        """Спрашивает у GitHub номер последней удачной сборки."""
        gh = find_gh()
        if not gh:
            if manual:
                self.show_banner("Для проверки обновлений нужен GitHub CLI. "
                                 "update.bat установит его и поможет войти в GitHub.",
                                 "Запустить update.bat", self.run_updater)
            return
        if manual:
            self.app_update_btn.configure(state="disabled", text="Проверяю…")

        def run():
            latest, err = None, ""
            try:
                p = subprocess.run(
                    [gh, "run", "list", "--repo", REPO, "--workflow", "build.yml",
                     "--status", "success", "--limit", "1",
                     "--json", "databaseId,createdAt"],
                    capture_output=True, timeout=30, creationflags=NO_WINDOW)
                runs = json.loads(p.stdout.decode("utf-8", "replace") or "[]")
                if runs:
                    latest = runs[0]
                err = p.stderr.decode("utf-8", "replace").strip()
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                err = str(e)
            self.events.put(("update_check", latest, err, manual))
        threading.Thread(target=run, daemon=True).start()

    def _on_update_check(self, latest, err, manual):
        self.app_update_btn.configure(state="normal", text="Проверить обновления")
        if latest is None:
            if err:
                self.log("Проверка обновлений: " + err)
            if manual:
                need_login = "auth login" in err or "authenticat" in err.lower()
                text = ("Нужно один раз войти в GitHub — это сделает update.bat."
                        if need_login else "Не удалось проверить обновления — подробности в журнале.")
                self.show_banner(text, "Запустить update.bat", self.run_updater)
            return
        run_id = latest.get("databaseId", 0)
        date = (latest.get("createdAt") or "")[:10]
        if run_id > BUILD_ID:
            self.show_banner(f"Вышла новая версия Downloader (сборка {run_id} от {date}).",
                             "Обновить", self.run_updater)
        elif manual:
            self.hide_banner()
            self.set_main_status(f"✓ У вас последняя версия (сборка {BUILD_ID})", GREEN)

    def run_updater(self):
        path = os.path.join(BASE_DIR, "update.bat")
        if not os.path.isfile(path):
            self.show_banner("Не найден update.bat рядом с программой. Скачайте его из "
                             "репозитория на GitHub и положите рядом с Downloader.exe.")
            return
        if self.workers_alive:
            self.show_banner("Сейчас идёт скачивание. Дождитесь окончания или нажмите "
                             "«Отмена», затем обновите.", "Обновить", self.run_updater)
            return
        # update.bat закроет программу, заменит exe и запустит новую версию
        subprocess.Popen(f'start "Обновление Downloader" "{path}" /auto',
                         shell=True, cwd=BASE_DIR)
        self.quit_app()

    # ------------------------------------------------------------ события из потоков
    def _poll_events(self):
        changed = False
        save_needed = False
        try:
            while True:
                e = self.events.get_nowait()
                kind = e[0]
                if kind == "log":
                    self.log(e[1])
                elif kind == "item_progress":
                    e[1].set_progress(e[2])
                    e[1].set_status(e[3])
                    changed = True
                elif kind == "title":
                    e[1].set_title(e[2])
                elif kind == "meta":
                    if e[1].winfo_exists():
                        e[1].set_title(e[2])
                        e[1].set_meta(e[3], e[4])
                elif kind == "auto_ytdlp":
                    self._on_auto_ytdlp(e[1])
                elif kind == "resolved":
                    if e[1].st in ("wait", "paused"):      # дальше качаем по найденной ссылке
                        e[1].url = e[2]
                        save_needed = True
                elif kind == "thumb":
                    if e[1].winfo_exists():
                        e[1].set_thumb(e[2])
                elif kind == "item_status":
                    e[1].set_status(e[2], e[3])
                    changed = True
                elif kind == "state":
                    e[1].set_state(e[2])
                    changed = True
                    save_needed = True
                elif kind == "result":
                    if e[1] is True:
                        self.batch_ok += 1
                    elif e[1] is False:
                        self.batch_fail += 1
                elif kind == "worker_done":
                    self.workers_alive -= 1
                    if self.workers_alive == 0:
                        self._finish_batch()
                elif kind == "updated":
                    self._tools_busy = False
                    self.update_btn.configure(state="normal")
                    self.progress.set(0)
                    self.set_main_status(e[1], ACCENT if "не удалось" in e[1] else GREEN)
                    self._check_tools()
                elif kind == "tools_progress":
                    self.progress.set(e[1])
                    self.set_main_status(e[2])
                elif kind == "update_check":
                    self._on_update_check(e[1], e[2], e[3])
                elif kind == "history":
                    self._add_history(e[1])
                elif kind == "tray_show":
                    self.show_window()
                elif kind == "tray_quit":
                    self.quit_app()
        except queue.Empty:
            pass
        if changed and self.workers_alive:
            self._update_overall()
        if save_needed:
            self.save_queue()
        self.after(100, self._poll_events)

    def _update_overall(self):
        batch = [i for i in self.items if i.batch]
        if batch:
            self.progress.set(sum(i.frac for i in batch) / len(batch))
        running = [i for i in self.items if i.st == "run"]
        waiting = sum(1 for i in self.items if i.st == "wait")
        paused = sum(1 for i in self.items if i.st == "paused")
        if len(running) == 1 and not waiting and not paused:
            self.set_main_status(running[0].last_text)
        else:
            text = f"Качается: {len(running)}   •   в очереди: {waiting}"
            if paused:
                text += f"   •   на паузе: {paused}"
            self.set_main_status(text + f"   •   готово: {self.batch_ok}")

    def _finish_batch(self):
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.update_btn.configure(state="normal")
        for item in self.items:
            item.batch = False
        ok, fail = self.batch_ok, self.batch_fail
        self.progress.set(1 if ok and not fail else 0)
        msg = f"Скачано: {ok}"
        if fail:
            msg += f"   •   не удалось: {fail} (подробности в журнале)"
        paused = sum(1 for i in self.items if i.st == "paused")
        if paused:
            msg += f"   •   на паузе: {paused}"
        self.set_main_status(msg, GREEN if ok and not fail else None)
        if self.v_sound.get():
            if not play_sound():
                self.bell()
            hidden = self.state() in ("withdrawn", "iconic") or self.focus_displayof() is None
            if hidden and (ok or fail):
                title = "Всё скачано" if ok and not fail else "Скачивание завершено"
                self.notify(title, msg.replace("   •   ", ", "))

    def on_close(self):
        """Крестик: сворачивает в трей (если включено), иначе закрывает программу."""
        if self.tray and self.v_tray.get():
            self.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.notify("Downloader работает в фоне",
                            "Значок в трее: двойной щелчок — открыть, правый — выход")
            return
        self.quit_app()

    def quit_app(self):
        self.save_queue()
        self.cancel()
        self._save()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()

    def proxy_for(self, url):
        """Адрес прокси для этой ссылки или None."""
        mode, proxy = self.settings.get("proxy_mode"), self.settings.get("proxy")
        if not proxy or mode == "Нет":
            return None
        if mode == "Все сайты" or is_youtube(url):
            return proxy
        return None

    # ------------------------------------------------------------ тихое обновление yt-dlp
    def _auto_ytdlp_tick(self):
        """Раз в час смотрит, не пора ли обновить yt-dlp (раз в сутки), и делает это тихо."""
        delay = 3600 * 1000
        try:
            due = time.time() - float(self.settings.get("ytdlp_checked") or 0) > 20 * 3600
            if self.v_auto_ytdlp.get() and due and not NO_SAVE and os.path.isfile(YTDLP):
                if self.workers_alive or self._tools_busy:
                    delay = 10 * 60 * 1000          # идёт скачивание — попробуем позже
                else:
                    self._silent_ytdlp_update()
        finally:
            self.after(delay, self._auto_ytdlp_tick)

    def _silent_ytdlp_update(self):
        self._tools_busy = True

        def run():
            try:
                p = subprocess.run([YTDLP, "-U"], capture_output=True, cwd=BASE_DIR,
                                   timeout=300, creationflags=NO_WINDOW)
                out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", "replace"))
            except (OSError, subprocess.TimeoutExpired) as e:
                out = str(e)
            ver = re.search(r"(stable|nightly|master)@[\d.]+", out)
            ver = ver.group(0) if ver else ""
            if "Updated yt-dlp" in out:
                result = ("updated", ver)
            elif "up to date" in out:
                result = ("same", ver)
            else:
                result = ("failed", out.strip().splitlines()[-1] if out.strip() else "")
            self.events.put(("auto_ytdlp", result))
        threading.Thread(target=run, daemon=True).start()

    def _on_auto_ytdlp(self, result):
        self._tools_busy = False
        kind, text = result
        if kind in ("updated", "same"):
            self.settings["ytdlp_checked"] = time.time()
            self._save()
        if kind == "updated":
            self.log(f"yt-dlp обновился автоматически: {text}")
            if not self.workers_alive:
                self.set_main_status(f"✓ yt-dlp обновился до {text}", GREEN)
        elif kind == "same":
            self.log(f"yt-dlp: последняя версия {text}")
        else:
            self.log(f"Автообновление yt-dlp не удалось: {text}")

    def _check_tools(self):
        """Если в папке программы чего-то не хватает — предлагает докачать."""
        missing = missing_tools()
        if missing:
            self.show_banner("В папке программы не хватает: " + ", ".join(missing) +
                             ". Без них скачивание работать не будет.",
                             "Скачать", self.update_ytdlp)

    # ------------------------------------------------------------ настройки
    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            return
        self.settings_win = SettingsWindow(self)

    def refresh_folder_label(self):
        text = self.settings["folder"]
        extra = []
        if self.v_org.get() != "Не раскладывать":
            extra.append(self.v_org.get().lower())
        if self.v_name.get() != "Название":
            extra.append("имя: " + self.v_name.get().lower())
        if extra:
            text += "   ·   " + ", ".join(extra)
        self.folder_label.configure(text=text)

    def toggle_autostart(self):
        ok = set_autostart(self.v_autostart.get())
        if not ok:
            self.v_autostart.set(False)
            self.set_main_status("Не удалось включить автозапуск", ACCENT)
        elif self.v_autostart.get():
            self.set_main_status("Downloader будет запускаться вместе с Windows (в трее)")
        self._save()

    # ------------------------------------------------------------ история
    def _add_history(self, item):
        rec = {"url": item.url, "title": item.full_title or item.url,
               "file": item.file_path,
               "date": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
               "opts": {k: v for k, v in item.opts.items() if k != "cookies"}}
        self.history.insert(0, rec)
        save_history(self.history)
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.refresh()

    def refresh_history_window(self):
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.refresh()

    def open_history(self):
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.deiconify()
            self.history_win.lift()
            self.history_win.refresh()
            return
        self.history_win = HistoryWindow(self)

    # ------------------------------------------------------------ перетаскивание
    def _setup_dnd(self):
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD._require(self)
        except Exception as e:
            self.log(f"Перетаскивание недоступно: {e}")
            return
        self.dnd_ready = True
        self._drop_cmd = self.register(self._on_drop)
        self._register_drop_tree(self)

    def _register_drop_tree(self, root):
        stack = [root]
        while stack:
            w = stack.pop()
            try:
                self.tk.call("tkdnd::drop_target", "register", w._w, ("DND_Text", "DND_Files"))
                self.tk.call("bind", w._w, "<<Drop>>", f"{self._drop_cmd} %D")
            except tk.TclError:
                pass
            stack.extend(w.winfo_children())

    def _on_drop(self, data):
        """Что-то бросили в окно: ссылку из браузера, ярлык .url или .txt со ссылками."""
        texts, lists = [data], []
        if not re.match(r"\s*https?://", data):
            try:
                paths = self.tk.splitlist(data)
            except tk.TclError:
                paths = []
            for path in paths:
                if os.path.isfile(path) and path.lower().endswith((".url", ".txt")):
                    try:
                        with open(path, encoding="utf-8", errors="replace") as f:
                            (lists if path.lower().endswith(".txt") else texts).append(f.read())
                    except OSError:
                        pass
        known = {i.url for i in self.items}
        urls = []
        for text in texts:
            for url in re.findall(r"https?://[^\s\"'<>]+", text):
                if url not in known and url not in urls:
                    urls.append(url)
        # .txt может быть списком песен без ссылок — их ищем по названию
        for text in lists:
            for url in parse_input(text):
                if url not in known and url not in urls:
                    urls.append(url)
        added = [u for u in urls if self.add_item(u)]
        self.set_main_status(f"Добавлено перетаскиванием: {len(added)}" if added
                             else "В перетащенном не нашлось ссылок")
        return "copy"

    # ------------------------------------------------------------ трей и уведомления
    def _setup_tray(self):
        if not pystray:
            return
        try:
            image = Image.open(ICON_PNG) if os.path.isfile(ICON_PNG) else \
                Image.new("RGB", (64, 64), ACCENT)
            menu = pystray.Menu(
                pystray.MenuItem("Открыть Downloader",
                                 lambda: self.events.put(("tray_show",)), default=True),
                pystray.MenuItem("Выход", lambda: self.events.put(("tray_quit",))))
            build = f" (сборка {BUILD_ID})" if BUILD_ID else ""
            self.tray = pystray.Icon("Downloader", image, f"Downloader {APP_VERSION}{build}", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            self.tray = None
            self.log(f"Значок в трее недоступен: {e}")

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def notify(self, title, text):
        if self.tray:
            try:
                self.tray.notify(text, title)
            except Exception:
                pass

    def _toggle_sponsor(self):
        self._save()
        if self.v_sponsor.get():
            self.set_main_status("YouTube: будут вырезаны реклама, самореклама и «подпишитесь» "
                                 "(по базе SponsorBlock)")

    def _toggle_chapters(self):
        self._save()
        if self.v_chapters.get():
            self.set_main_status("Видео с главами сохранится папкой с отдельным файлом "
                                 "на каждую главу")

    def _toggle_sound(self):
        self._save()
        if self.v_sound.get():
            play_sound()


# ================================================================ самопроверка
def measure_cover(path):
    """Размер вшитой обложки (ширина, высота) — через ffmpeg."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True,
                       creationflags=NO_WINDOW)
    m = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", p.stderr.decode("utf-8", "replace"))
    return (int(m[1]), int(m[2])) if m else None


def measure_loudness(path):
    """Средняя громкость трека в LUFS."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True, creationflags=NO_WINDOW)
    vals = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", p.stderr.decode("utf-8", "replace"))
    return float(vals[-1]) if vals else None


def selftest(url, out_dir, page_url=None, proxy_url=None):
    """Downloader.exe --selftest URL ПАПКА [СТРАНИЦА] — проверка сборки на GitHub.
    Открывает окно, затем качает URL в разных форматах. СТРАНИЦА — «клип» с обложкой
    и субтитрами для проверки музыкальных функций. Код выхода 0 — всё хорошо."""
    global NO_SAVE, FORCE_STYLE
    NO_SAVE = True
    os.makedirs(out_dir, exist_ok=True)
    log_file = open(os.path.join(out_dir, "selftest.log"), "w", encoding="utf-8")
    failed = 0

    def log(msg):
        log_file.write(msg + "\n")
        log_file.flush()

    log(f"Downloader {APP_VERSION}, сборка {BUILD_ID}")
    log(f"yt-dlp: {YTDLP} ({'есть' if os.path.isfile(YTDLP) else 'НЕТ'})")
    log(f"ffmpeg: {FFMPEG or 'НЕТ'}")
    log(f"deno:   {DENO or 'НЕТ'}")
    for name, ok in (("yt-dlp", os.path.isfile(YTDLP)), ("ffmpeg", FFMPEG), ("deno", DENO)):
        if not ok:
            failed += 1
            log(f"FAIL нет {name}")

    # 1. окно открывается без ошибок — в обоих оформлениях, с окнами настроек и истории
    for style in ("phantom", "classic"):
        FORCE_STYLE = style
        try:
            app = App()
            app.update()
            app.open_settings()
            app.open_history()
            app.open_bulk()
            app.update()
            app.quit_app()
            log(f"OK   окно программы ({style})")
        except Exception as e:
            failed += 1
            log(f"FAIL окно программы ({style}): {e!r}")
    FORCE_STYLE = None

    # 2. скачивание в разных форматах
    base = {"playlist": False, "subs": False, "embed": True, "exact": True,
            "cookies": None, "sponsor": False, "chapters": False, "cut": None}
    cases = [
        ("video-mp4", dict(base, mode="Видео", quality="1080p", fmt="MP4"), ".mp4"),
        ("video-mkv", dict(base, mode="Видео", quality="720p", fmt="MKV"), ".mkv"),
        # тестовое видео в H.264 в WEBM не помещается — должен получиться MKV
        ("video-webm", dict(base, mode="Видео", quality="720p", fmt="WEBM"), (".webm", ".mkv")),
        ("audio-mp3", dict(base, mode="Аудио", quality="Лучшее", fmt="MP3"), ".mp3"),
        ("audio-flac-cut", dict(base, mode="Аудио", quality="Лучшее", fmt="FLAC", cut=(1, 3)),
         ".flac"),
        # раскладка по каналу: у тестового видео нет автора -> папка «Без автора»
        ("layout-channel", dict(base, mode="Видео", quality="720p", fmt="MP4",
                                layout={"org": "По каналу", "name": "Канал - Название"}),
         os.path.join("Без автора", "test.mp4")),
        ("layout-custom", dict(base, mode="Аудио", quality="Лучшее", fmt="MP3",
                               layout={"org": "Не раскладывать", "name": "Свой шаблон",
                                       "custom": "Моя папка/%(title)s (%(id)s)"}),
         os.path.join("Моя папка", "test (test).mp3")),
    ]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        p = subprocess.run(preview_command(url, cases[0][1]), capture_output=True, timeout=120,
                           env=env, creationflags=NO_WINDOW)
        info = json.loads(p.stdout.decode("utf-8", "replace") or "null")
        log(f"OK   превью: {summarize(info, cases[0][1])}" if info else "FAIL превью: пусто")
        failed += 0 if info else 1
    except Exception as e:
        failed += 1
        log(f"FAIL превью: {e!r}")
    for name, opts, ext in cases:
        folder = os.path.join(out_dir, name)
        try:
            p = subprocess.run(build_command(url, opts, folder), capture_output=True,
                               timeout=300, env=env, creationflags=NO_WINDOW)
            files = sorted(os.path.relpath(os.path.join(root, f), folder)
                           for root, _, names in os.walk(folder) for f in names)
            files = [f for f in files if f.endswith(ext)]
            ok = p.returncode == 0 and files
            log(f"{'OK  ' if ok else 'FAIL'} {name}: код {p.returncode}, файлы {files}")
            if not ok:
                failed += 1
                out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", "replace"))
                log("\n".join("      " + ln for ln in out.splitlines()[-20:]))
        except Exception as e:
            failed += 1
            log(f"FAIL {name}: {e!r}")

    # 2б. скачивание через прокси (как у пользователей с VPN-программой)
    if proxy_url:
        folder = os.path.join(out_dir, "proxy")
        opts = dict(base, mode="Видео", quality="720p", fmt="MP4", proxy_url=proxy_url)
        try:
            p = subprocess.run(build_command(url, opts, folder), capture_output=True,
                               timeout=300, env=env, creationflags=NO_WINDOW)
            files = os.listdir(folder) if os.path.isdir(folder) else []
            ok = p.returncode == 0 and any(f.endswith(".mp4") for f in files)
            failed += 0 if ok else 1
            log(f"{'OK  ' if ok else 'FAIL'} через прокси {proxy_url}: код {p.returncode}, "
                f"файлы {files}")
            if not ok:
                out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", "replace"))
                log("\n".join("      " + ln for ln in out.splitlines()[-15:]))
        except Exception as e:
            failed += 1
            log(f"FAIL через прокси: {e!r}")

    # 3. музыка: квадратная обложка, громкость, тексты
    if page_url:
        opts = dict(base, mode="Аудио", quality="Лучшее", fmt="MP3",
                    square=True, normalize=True, lyrics=True)
        folder = os.path.join(out_dir, "music")
        try:
            p = subprocess.run(build_command(page_url, opts, folder), capture_output=True,
                               timeout=300, env=env, creationflags=NO_WINDOW)
            out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", "replace"))
            files = [ln[3:] for ln in out.splitlines() if ln.startswith("[F]")]
            notes = postprocess_audio(files, opts) if p.returncode == 0 else []
            mp3 = audio_files(files) or audio_files([folder])
            on_disk = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
            log(f"      файлы: {on_disk}")
            checks = []
            if p.returncode != 0 or not mp3:
                checks.append(("скачивание", False, f"код {p.returncode}"))
            else:
                cover = measure_cover(mp3[0])
                checks.append(("квадратная обложка", bool(cover) and cover[0] == cover[1],
                               f"{cover[0]}x{cover[1]}" if cover else "нет обложки"))
                lufs = measure_loudness(mp3[0])
                checks.append(("громкость", lufs is not None and abs(lufs + 14) <= 2,
                               f"{lufs} LUFS (нужно около -14)"))
                lrc = os.path.splitext(mp3[0])[0] + ".lrc"
                text = open(lrc, encoding="utf-8", errors="replace").read() \
                    if os.path.isfile(lrc) else ""
                checks.append(("текст песни .lrc", "Первая строка" in text,
                               os.path.basename(lrc) if text else "файла нет"))
            for name, ok, detail in checks:
                failed += 0 if ok else 1
                log(f"{'OK  ' if ok else 'FAIL'} музыка — {name}: {detail}")
            if notes:
                log("      " + "; ".join(notes))
            if not all(ok for _, ok, _ in checks):
                log("\n".join("      " + ln for ln in out.splitlines()[-20:]))
        except Exception as e:
            failed += 1
            log(f"FAIL музыка: {e!r}")

    log(f"Итог: {'ВСЁ ХОРОШО' if not failed else f'ошибок: {failed}'}")
    log_file.close()
    return 1 if failed else 0


def screenshot(out_path, style, urls):
    """Downloader.exe --screenshot ФАЙЛ.png СТИЛЬ ССЫЛКА... — снимок окна для проверки дизайна."""
    global NO_SAVE, FORCE_STYLE
    NO_SAVE, FORCE_STYLE = True, style
    from PIL import ImageGrab
    app = App()
    app.geometry("900x760+0+0")
    for url in urls:
        app.add_item(url)
    if app.items:
        app.items[0].set_state("run")
        app.items[0].set_progress(0.42)
        app.items[0].set_status("42.0%   •   8.31MiB/s   •   осталось 00:07")
    if len(app.items) > 1:
        app.items[-1].set_state("done")
        app.items[-1].set_status("✓ Готово", GREEN)
    app.set_main_status("Качается: 1   •   в очереди: 1   •   готово: 1")
    app.progress.set(0.47)

    def grab():
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out_path)
        app.quit_app()
    app.after(9000, grab)          # ждём превью и обложки
    app.mainloop()
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--screenshot":
        sys.exit(screenshot(sys.argv[2], sys.argv[3], sys.argv[4:]))
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        extra = sys.argv[4:] + [None, None]
        sys.exit(selftest(sys.argv[2], sys.argv[3], extra[0] or None, extra[1] or None))
    App().mainloop()
