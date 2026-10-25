"""
Downloader — графическая оболочка для yt-dlp на CustomTkinter.

Лежит в одной папке с yt-dlp.exe, ffmpeg.exe и deno.exe.
Настройки хранятся в settings.json рядом с программой.
"""
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageDraw

APP_VERSION = "1.2.0"
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


def find_tool(name):
    """Ищет exe рядом с программой, в текущей папке, в подпапках и в PATH."""
    places = [BASE_DIR, os.getcwd(),
              os.path.join(BASE_DIR, "Downloader"), os.path.join(BASE_DIR, "bin")]
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


YTDLP = find_tool("yt-dlp.exe") or os.path.join(BASE_DIR, "yt-dlp.exe")
FFMPEG = find_tool("ffmpeg.exe")
# deno нужен YouTube для расшифровки ссылок; официальный установщик кладёт его в ~/.deno/bin
DENO = find_tool("deno.exe") or next(
    (p for p in [os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe")]
     if os.path.isfile(p)), None)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
DEFAULT_FOLDER = os.path.join(BASE_DIR, "Downloads")

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
VIDEO_FORMATS = {           # формат: (контейнер для склейки, предпочтения по расширению)
    "MP4": ("mp4", "ext:mp4:m4a"),
    "MKV": ("mkv", None),
    "WEBM": ("webm/mkv", "ext:webm:webm"),
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
    "parallel": "3",        # сколько ссылок качать одновременно
    "fragments": 8,         # сколько кусков одного видео качать параллельно
    "clip_watch": False,    # ловить ссылки из буфера обмена
    "check_updates": True,  # проверять обновления при запуске
}

ACCENT = "#E5484D"
ACCENT_HOVER = "#C93C41"
GREEN = ("#1a7f37", "#3fb950")
GRAY_TEXT = ("gray40", "gray60")
THUMB_W, THUMB_H = 128, 72

# Коды клавиш Windows не зависят от раскладки: V=86, C=67, X=88, A=65
CTRL_KEYCODES = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}


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


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
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
    if opts["playlist"]:
        # плейлист складывается в отдельную папку с его названием
        cmd += ["--yes-playlist", "-o", "%(playlist_title&{}/|)s%(title)s.%(ext)s"]
    else:
        cmd += ["--no-playlist", "-o", "%(title)s.%(ext)s"]

    cmd += format_args(opts)
    if opts["mode"] == "Аудио":
        ext, lossless = AUDIO_FORMATS[opts["fmt"]]
        cmd += ["-x", "--audio-format", ext]
        if not lossless:
            cmd += ["--audio-quality", AUDIO_QUALITIES[opts["quality"]][0]]
    else:
        cmd += ["--merge-output-format", VIDEO_FORMATS[opts["fmt"]][0]]
        if opts["subs"]:
            cmd += ["--write-subs", "--sub-langs", "ru.*,en.*", "--embed-subs"]
    if opts["embed"]:
        if opts["fmt"] not in NO_THUMB_FORMATS:
            cmd += ["--embed-thumbnail"]
        cmd += ["--embed-metadata"]
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
    parts = [info.get("uploader") or info.get("channel"), dur_text,
             f"≈ {size_text}" if size_text else None, quality]
    return {"title": info.get("title"),
            "meta": "   •   ".join(p for p in parts if p),
            "thumb": pick_thumbnail(info)}


def load_thumbnail(url):
    """Скачивает обложку и делает из неё картинку 16:9 со скруглёнными углами."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
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


def explain_error(err, code):
    """Короткое понятное описание ошибки yt-dlp."""
    low = err.lower()
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
        super().__init__(master, corner_radius=10, fg_color=("gray90", "gray17"))
        self.app = app
        self.url = url
        self.opts = opts
        self.st = "wait"             # wait / run / done / error / cancel
        self.frac = 0.0              # прогресс 0..1
        self.batch = False           # входит в текущую пачку загрузок
        self.last_text = ""
        self.file_path = None
        self.thumb_image = None

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
                                  corner_radius=8, fg_color=("gray80", "gray25"),
                                  font=ctk.CTkFont(size=12, weight="bold"))
        self.thumb.grid(row=0, column=0, rowspan=4, padx=(8, 12), pady=8)

        self.title_lbl = ctk.CTkLabel(self, text=url, anchor="w",
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
        self.bar.grid(row=3, column=1, sticky="ew", pady=(2, 8))
        self.bar.grid_remove()

        self.btn = ctk.CTkButton(self, text="✕", width=30, height=30,
                                 fg_color="transparent",
                                 hover_color=("gray80", "gray28"),
                                 text_color=("gray30", "gray70"),
                                 command=self.on_button)
        self.btn.grid(row=0, column=2, rowspan=4, padx=8)

    def set_title(self, text):
        if text:
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
            self.bar.grid()
            self.set_progress(0)
        else:
            self.bar.grid_remove()
            self.btn.configure(state="normal", text="📂" if state == "done" else "✕")
            if state in ("done", "error"):
                self.frac = 1.0
            elif state == "wait":
                self.frac = 0.0

    def on_button(self):
        if self.st == "done" and self.file_path:
            self.app.show_in_folder(self.file_path)
        elif self.st != "run":
            self.app.remove_item(self)


# ================================================================ главное окно
class App(ctk.CTk):
    def __init__(self):
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings["theme"])
        ctk.set_default_color_theme("blue")
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
        self.workers_alive = 0
        self.batch_ok = self.batch_fail = 0
        self.cancel_flag = threading.Event()
        self.preview_slots = threading.Semaphore(3)   # не больше 3 превью одновременно
        self._last_clip = self._read_clipboard()

        self._build_ui()
        self._apply_mode()
        self._setup_clipboard_keys()
        self.after(100, self._poll_events)
        self.after(300, self._autopaste)
        self.after(700, self._watch_clipboard)
        if self.settings["check_updates"] and getattr(sys, "frozen", False):
            self.after(2000, lambda: self.check_updates(manual=False))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log(f"Downloader {APP_VERSION}{build}")
        self.log(f"Папка программы: {BASE_DIR}")
        self.log(f"yt-dlp: {YTDLP if os.path.isfile(YTDLP) else 'НЕ НАЙДЕН'}")
        self.log(f"ffmpeg: {FFMPEG or 'НЕ НАЙДЕН (MP3 и склейка видео работать не будут)'}")
        self.log(f"deno:   {DENO or 'НЕ НАЙДЕН (YouTube может отдавать не все форматы)'}")
        problems = [name for name, ok in (("yt-dlp.exe", os.path.isfile(YTDLP)),
                                          ("ffmpeg.exe", FFMPEG), ("deno.exe", DENO)) if not ok]
        if problems:
            self.set_main_status("⚠ Не найдено: " + ", ".join(problems) +
                                 " — положите рядом с Downloader.exe", ACCENT)

    # ------------------------------------------------------------ интерфейс
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)
        pad = {"padx": 20}
        secondary = dict(fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
                         text_color=("gray10", "gray90"))
        outline = dict(fg_color="transparent", border_width=1,
                       text_color=("gray20", "gray80"))

        # --- шапка
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(16, 6), **pad)
        head.grid_columnconfigure(1, weight=1)
        if os.path.isfile(ICON_PNG):
            logo_img = Image.open(ICON_PNG)
            self.logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(52, 52))
            ctk.CTkLabel(head, text="", image=self.logo).grid(row=0, column=0, rowspan=2,
                                                              padx=(0, 12))
        ctk.CTkLabel(head, text="Downloader",
                     font=ctk.CTkFont(size=26, weight="bold")).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(head, text="YouTube, VK, TikTok и ещё 1000+ сайтов",
                     text_color=GRAY_TEXT).grid(row=1, column=1, sticky="nw")
        switches = ctk.CTkFrame(head, fg_color="transparent")
        switches.grid(row=0, column=2, rowspan=2, sticky="e")
        self.theme_switch = ctk.CTkSwitch(switches, text="Тёмная тема", command=self.toggle_theme)
        self.theme_switch.pack(anchor="w", pady=2)
        if self.settings["theme"] == "dark":
            self.theme_switch.select()
        self.v_clip = ctk.BooleanVar(value=self.settings["clip_watch"])
        ctk.CTkSwitch(switches, text="Ловить ссылки из буфера", variable=self.v_clip,
                      command=self.toggle_clip_watch).pack(anchor="w", pady=2)

        # --- плашка «вышла новая версия» (скрыта, пока не нужна)
        self.banner = ctk.CTkFrame(self, corner_radius=10, fg_color=("#FDECEC", "#3A1D1F"),
                                   border_width=1, border_color=ACCENT)
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_lbl = ctk.CTkLabel(self.banner, text="", anchor="w", justify="left",
                                       text_color=("gray10", "gray90"))
        self.banner_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        self.banner_btn = ctk.CTkButton(self.banner, text="Обновить", width=110,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.banner_btn.grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(self.banner, text="✕", width=30, fg_color="transparent",
                      hover_color=("gray80", "gray28"), text_color=("gray30", "gray70"),
                      command=self.hide_banner).grid(row=0, column=2, padx=(0, 8))

        # --- ссылка
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.grid(row=2, column=0, sticky="ew", pady=8, **pad)
        url_row.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(url_row, height=42, corner_radius=10,
                                      placeholder_text="Вставьте ссылку (Ctrl+V) и нажмите Enter…",
                                      font=ctk.CTkFont(size=14))
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.bind("<Return>", lambda e: self.add_from_entry())
        ctk.CTkButton(url_row, text="Вставить", width=90, height=42, corner_radius=10,
                      command=self.paste_clipboard, **secondary).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(url_row, text="+ В очередь", width=110, height=42, corner_radius=10,
                      command=self.add_from_entry).grid(row=0, column=2, padx=(8, 0))

        # --- настройки
        card = ctk.CTkFrame(self, corner_radius=14)
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

        par = ctk.CTkFrame(card, fg_color="transparent")
        par.grid(row=0, column=5, sticky="e", padx=14, pady=(14, 8))
        ctk.CTkLabel(par, text="Одновременно:").pack(side="left", padx=(0, 6))
        self.parallel = ctk.CTkOptionMenu(par, values=PARALLEL_CHOICES, width=60,
                                          command=lambda _: self._save())
        self.parallel.set(str(self.settings["parallel"]))
        self.parallel.pack(side="left")

        flags = ctk.CTkFrame(card, fg_color="transparent")
        flags.grid(row=1, column=0, columnspan=6, sticky="w", padx=14, pady=(0, 10))
        self.v_playlist = ctk.BooleanVar(value=self.settings["playlist"])
        self.v_subs = ctk.BooleanVar(value=self.settings["subs"])
        self.v_embed = ctk.BooleanVar(value=self.settings["embed"])
        ctk.CTkCheckBox(flags, text="Весь плейлист", variable=self.v_playlist,
                        command=self._save).pack(side="left", padx=(0, 18))
        self.subs_box = ctk.CTkCheckBox(flags, text="Субтитры (ru/en)", variable=self.v_subs,
                                        command=self._save)
        self.subs_box.pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(flags, text="Обложка и теги", variable=self.v_embed,
                        command=self._save).pack(side="left")

        # фрагмент и cookies
        extra = ctk.CTkFrame(card, fg_color="transparent")
        extra.grid(row=2, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 10))
        extra.grid_columnconfigure(6, weight=1)
        ctk.CTkLabel(extra, text="✂  Фрагмент с").grid(row=0, column=0, padx=(0, 6))
        self.cut_from = ctk.CTkEntry(extra, width=70, placeholder_text="0:00")
        self.cut_from.grid(row=0, column=1)
        ctk.CTkLabel(extra, text="до").grid(row=0, column=2, padx=6)
        self.cut_to = ctk.CTkEntry(extra, width=70, placeholder_text="конец")
        self.cut_to.grid(row=0, column=3)
        ctk.CTkButton(extra, text="✕", width=28, height=28, fg_color="transparent",
                      hover_color=("gray80", "gray28"), text_color=("gray30", "gray70"),
                      command=self.clear_cut).grid(row=0, column=4, padx=(4, 8))
        self.v_exact = ctk.BooleanVar(value=self.settings["exact_cut"])
        ctk.CTkCheckBox(extra, text="точно по секундам", variable=self.v_exact,
                        command=self._save).grid(row=0, column=5)
        ctk.CTkLabel(extra, text="🍪 Cookies:").grid(row=0, column=7, padx=(12, 6))
        self.cookies = ctk.CTkOptionMenu(extra, values=list(COOKIE_SOURCES), width=150,
                                         command=self._on_cookies)
        self.cookies.set(self.settings["cookies"] if self.settings["cookies"] in COOKIE_SOURCES
                         else "Нет")
        self.cookies.grid(row=0, column=8)

        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=3, column=0, columnspan=6, sticky="ew", padx=14, pady=(0, 14))
        folder_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(folder_row, text="📁").grid(row=0, column=0, padx=(0, 6))
        self.folder_label = ctk.CTkLabel(folder_row, text=self.settings["folder"], anchor="w",
                                         text_color=("gray30", "gray70"))
        self.folder_label.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(folder_row, text="Изменить", width=90,
                      command=self.choose_folder).grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(folder_row, text="Открыть", width=90, command=self.open_folder,
                      **secondary).grid(row=0, column=3, padx=(8, 0))

        # --- очередь
        qhead = ctk.CTkFrame(self, fg_color="transparent")
        qhead.grid(row=4, column=0, sticky="ew", pady=(8, 0), **pad)
        qhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(qhead, text="Очередь",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(qhead, text="Убрать готовые", width=120, height=26,
                      command=self.clear_finished, **outline).grid(row=0, column=1)

        self.queue_frame = ctk.CTkScrollableFrame(self, corner_radius=14)
        self.queue_frame.grid(row=5, column=0, sticky="nsew", pady=6, **pad)
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.queue_frame,
                                        text="Здесь появятся ссылки для скачивания",
                                        text_color=("gray50", "gray50"))
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
        self.start_btn = ctk.CTkButton(btns, text="⬇  Скачать", width=150, height=42,
                                       corner_radius=10, fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER,
                                       font=ctk.CTkFont(size=15, weight="bold"),
                                       command=self.start)
        self.start_btn.grid(row=0, column=0)
        self.cancel_btn = ctk.CTkButton(btns, text="Отмена", width=100, height=42,
                                        corner_radius=10, state="disabled",
                                        command=self.cancel, **secondary)
        self.cancel_btn.grid(row=0, column=1, padx=8)
        self.log_btn = ctk.CTkButton(btns, text="Журнал ▾", width=100, height=42,
                                     corner_radius=10, command=self.toggle_log, **outline)
        self.log_btn.grid(row=0, column=2)
        self.update_btn = ctk.CTkButton(btns, text="Обновить yt-dlp", width=130, height=42,
                                        corner_radius=10, command=self.update_ytdlp, **outline)
        self.update_btn.grid(row=0, column=4, padx=(0, 8))
        self.app_update_btn = ctk.CTkButton(btns, text="Проверить обновления", width=170,
                                            height=42, corner_radius=10,
                                            command=lambda: self.check_updates(manual=True),
                                            **outline)
        self.app_update_btn.grid(row=0, column=5)

        # --- журнал (скрыт по умолчанию)
        self.log_box = ctk.CTkTextbox(self, height=130, corner_radius=10,
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
            self.subs_box.configure(state="disabled")
        else:
            self.quality.configure(values=list(VIDEO_QUALITIES))
            self.quality.set(self.settings["video_quality"])
            self.fmt.configure(values=list(VIDEO_FORMATS))
            self.fmt.set(self.settings["video_format"])
            self.subs_box.configure(state="normal")
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
                self.cookies.set(self.settings["cookies"])
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
        src = COOKIE_SOURCES.get(self.cookies.get())
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
                             parallel=self.parallel.get(), clip_watch=self.v_clip.get(),
                             exact_cut=self.v_exact.get())
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
                "cut": cut, "exact": self.v_exact.get(),
                "cookies": self.cookie_args()}

    def toggle_theme(self):
        theme = "dark" if self.theme_switch.get() else "light"
        ctk.set_appearance_mode(theme)
        self.settings["theme"] = theme
        self._save()

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.settings["folder"],
                                       title="Папка для загрузок")
        if path:
            self.settings["folder"] = os.path.normpath(path)
            self.folder_label.configure(text=self.settings["folder"])
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
        added = False
        # можно вставить сразу несколько ссылок через пробел или с новой строки
        for url in self.url_entry.get().split():
            if url.startswith("http"):
                if not self.add_item(url):
                    return False
                added = True
        if added:
            self.url_entry.delete(0, "end")
            self._last_clip = self._read_clipboard()   # чтобы перехват не добавил её ещё раз
        return added

    def add_item(self, url):
        try:
            opts = self.current_opts()
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
        threading.Thread(target=self._fetch_preview, args=(item,), daemon=True).start()
        # если загрузка уже идёт, а свободные потоки закончились — запускаем ещё один
        if self.workers_alive and self.workers_alive < int(self.parallel.get()):
            self._spawn_worker()
        return True

    def remove_item(self, item):
        with self.lock:
            if item.st == "run":
                return
            self.items.remove(item)
        item.destroy()
        self._regrid()

    def clear_finished(self):
        with self.lock:
            gone = [i for i in self.items if i.st in ("done", "error", "cancel")]
            for item in gone:
                self.items.remove(item)
        for item in gone:
            item.destroy()
        self._regrid()

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
                p = subprocess.run(preview_command(item.url, item.opts), capture_output=True,
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
            s = summarize(info, item.opts)
            self.events.put(("meta", item, s["title"], s["meta"] or "—", None))
        if s.get("thumb"):
            try:
                self.events.put(("thumb", item, load_thumbnail(s["thumb"])))
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
        for item in self.items:              # повторить неудачные
            if item.st in ("error", "cancel"):
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
        for _ in range(min(int(self.parallel.get()), len(waiting))):
            self._spawn_worker()

    def _spawn_worker(self):
        self.workers_alive += 1
        threading.Thread(target=self._work, daemon=True).start()

    def cancel(self):
        self.cancel_flag.set()
        with self.lock:
            procs = list(self.procs)
        for p in procs:
            kill_tree(p)

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
        cmd = build_command(item.url, item.opts, self.settings["folder"],
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

        last_error = ""
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
                ev(("log", "✓ " + line[3:]))
            else:
                ev(("log", line))
                if line.startswith("ERROR"):
                    last_error = line.replace("ERROR:", "").strip()
        code = proc.wait()
        with self.lock:
            self.procs.discard(proc)

        if self.cancel_flag.is_set():
            ev(("state", item, "cancel"))
            ev(("item_status", item, "Отменено", None))
            return None
        if code == 0:
            ev(("state", item, "done"))
            ev(("item_status", item, "✓ Готово", GREEN))
            return True
        ev(("state", item, "error"))
        ev(("item_status", item, explain_error(last_error, code), ACCENT))
        return False

    def update_ytdlp(self):
        if self.workers_alive:
            return
        self.update_btn.configure(state="disabled")
        self.set_main_status("Обновляю yt-dlp…")
        if not self.log_visible:
            self.toggle_log()

        def run():
            try:
                p = subprocess.run([YTDLP, "-U"], capture_output=True, cwd=BASE_DIR,
                                   creationflags=NO_WINDOW)
                out = (p.stdout + p.stderr).decode("utf-8", "replace")
            except OSError as e:
                out = f"Не удалось запустить yt-dlp: {e}"
            out = ANSI.sub("", out)
            for line in out.splitlines():
                self.events.put(("log", line))
            ver = re.search(r"(stable|nightly|master)@[\d.]+", out)
            ver = ver.group(0) if ver else ""
            if "up to date" in out:
                msg = f"✓ У вас уже последняя версия yt-dlp ({ver})"
            elif "Updated yt-dlp" in out:
                msg = f"✓ yt-dlp обновлён до {ver}"
            else:
                msg = "Не удалось обновить yt-dlp — подробности в журнале"
            self.events.put(("updated", msg))
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
        self.on_close()

    # ------------------------------------------------------------ события из потоков
    def _poll_events(self):
        changed = False
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
                elif kind == "thumb":
                    if e[1].winfo_exists():
                        e[1].set_thumb(e[2])
                elif kind == "item_status":
                    e[1].set_status(e[2], e[3])
                    changed = True
                elif kind == "state":
                    e[1].set_state(e[2])
                    changed = True
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
                    self.update_btn.configure(state="normal")
                    self.set_main_status(e[1])
                elif kind == "update_check":
                    self._on_update_check(e[1], e[2], e[3])
        except queue.Empty:
            pass
        if changed and self.workers_alive:
            self._update_overall()
        self.after(100, self._poll_events)

    def _update_overall(self):
        batch = [i for i in self.items if i.batch]
        if batch:
            self.progress.set(sum(i.frac for i in batch) / len(batch))
        running = [i for i in self.items if i.st == "run"]
        waiting = sum(1 for i in self.items if i.st == "wait")
        if len(running) == 1 and not waiting:
            self.set_main_status(running[0].last_text)
        else:
            self.set_main_status(f"Качается: {len(running)}   •   в очереди: {waiting}"
                                 f"   •   готово: {self.batch_ok}")

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
        if self.cancel_flag.is_set():
            msg += "   •   остановлено"
        self.set_main_status(msg, GREEN if ok and not fail else None)
        self.bell()

    def on_close(self):
        self.cancel()
        self._save()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
