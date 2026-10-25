"""
Downloader — графическая оболочка для yt-dlp на CustomTkinter.

Лежит в одной папке с yt-dlp.exe, ffmpeg.exe и deno.exe.
Настройки хранятся в settings.json рядом с программой.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

# ---------------------------------------------------------------- пути
if getattr(sys, "frozen", False):           # запущено как .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:                                       # запущено как .py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def find_tool(name):
    """Ищет exe рядом с программой, в текущей папке, в подпапках и в PATH."""
    places = [BASE_DIR, os.getcwd(),
              os.path.join(BASE_DIR, "Downloader"), os.path.join(BASE_DIR, "bin")]
    for folder in places:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return shutil.which(name)


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

VIDEO_QUALITIES = {
    "Лучшее": None,
    "2160p (4K)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
AUDIO_QUALITIES = {
    "Лучшее": "0",
    "320 kbps": "320K",
    "192 kbps": "192K",
    "128 kbps": "128K",
}
PARALLEL_CHOICES = ["1", "2", "3", "4", "5"]

DEFAULTS = {
    "folder": DEFAULT_FOLDER,
    "mode": "Видео",
    "video_quality": "1080p",
    "audio_quality": "Лучшее",
    "playlist": False,
    "subs": False,
    "embed": True,
    "theme": "dark",
    "parallel": "3",        # сколько ссылок качать одновременно
    "fragments": 8,         # сколько кусков одного видео качать параллельно
}

APP_VERSION = "1.0.0"

ACCENT = "#E5484D"
ACCENT_HOVER = "#C93C41"
GREEN = ("#1a7f37", "#3fb950")
GRAY_TEXT = ("gray40", "gray60")

# Коды клавиш Windows не зависят от раскладки: V=86, C=67, X=88, A=65
CTRL_KEYCODES = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}


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


def build_command(url, opts, folder, fragments=8):
    """Собирает команду yt-dlp для одной ссылки."""
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
        cmd += ["--yes-playlist",
                "-o", "%(playlist_title&{}/|)s%(title)s.%(ext)s"]
    else:
        cmd += ["--no-playlist", "-o", "%(title)s.%(ext)s"]

    if opts["mode"] == "Аудио":
        cmd += ["-x", "--audio-format", "mp3",
                "--audio-quality", AUDIO_QUALITIES[opts["quality"]]]
    else:
        h = VIDEO_QUALITIES[opts["quality"]]
        sort = f"res:{h},ext:mp4:m4a" if h else "res,ext:mp4:m4a"
        cmd += ["-f", "bv*+ba/b", "-S", sort, "--merge-output-format", "mp4"]
        if opts["subs"]:
            cmd += ["--write-subs", "--sub-langs", "ru.*,en.*", "--embed-subs"]

    if opts["embed"]:
        cmd += ["--embed-thumbnail", "--embed-metadata"]
    if FFMPEG:
        cmd += ["--ffmpeg-location", os.path.dirname(FFMPEG)]
    if DENO:
        cmd += ["--js-runtimes", f"deno:{DENO}"]
    return cmd


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

        self.grid_columnconfigure(1, weight=1)

        tag = "MP3" if opts["mode"] == "Аудио" else opts["quality"].split(" ")[0]
        if opts["playlist"]:
            tag += " · плейлист"
        self.badge = ctk.CTkLabel(self, text=tag, width=86, corner_radius=6,
                                  fg_color=("gray80", "gray25"),
                                  font=ctk.CTkFont(size=12, weight="bold"))
        self.badge.grid(row=0, column=0, rowspan=3, padx=(10, 10), pady=8)

        self.title_lbl = ctk.CTkLabel(self, text=url, anchor="w",
                                      font=ctk.CTkFont(size=13))
        self.title_lbl.grid(row=0, column=1, sticky="ew", pady=(8, 0))

        self.status_lbl = ctk.CTkLabel(self, text="В очереди", anchor="w",
                                       text_color=GRAY_TEXT, font=ctk.CTkFont(size=12))
        self.status_lbl.grid(row=1, column=1, sticky="ew")

        self.bar = ctk.CTkProgressBar(self, height=4, progress_color=ACCENT)
        self.bar.set(0)
        self.bar.grid(row=2, column=1, sticky="ew", pady=(2, 10))
        self.bar.grid_remove()

        self.btn = ctk.CTkButton(self, text="✕", width=30, height=30,
                                 fg_color="transparent",
                                 hover_color=("gray80", "gray28"),
                                 text_color=("gray30", "gray70"),
                                 command=self.on_button)
        self.btn.grid(row=0, column=2, rowspan=3, padx=8)

    def set_title(self, text):
        self.title_lbl.configure(text=text if len(text) < 70 else text[:67] + "…")

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

        self.title(f"Downloader {APP_VERSION}")
        self.geometry("800x700")
        self.minsize(660, 580)

        self.items = []
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.procs = set()
        self.workers_alive = 0
        self.batch_ok = self.batch_fail = 0
        self.cancel_flag = threading.Event()

        self._build_ui()
        self._apply_mode()
        self._setup_clipboard_keys()
        self.after(100, self._poll_events)
        self.after(300, self._autopaste)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

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
        self.grid_rowconfigure(4, weight=1)
        pad = {"padx": 20}
        secondary = dict(fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
                         text_color=("gray10", "gray90"))
        outline = dict(fg_color="transparent", border_width=1,
                       text_color=("gray20", "gray80"))

        # --- шапка
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(18, 6), **pad)
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Downloader",
                     font=ctk.CTkFont(size=26, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text="YouTube, VK, TikTok и ещё 1000+ сайтов",
                     text_color=GRAY_TEXT).grid(row=1, column=0, sticky="w")
        self.theme_switch = ctk.CTkSwitch(head, text="Тёмная тема", command=self.toggle_theme)
        self.theme_switch.grid(row=0, column=1, rowspan=2)
        if self.settings["theme"] == "dark":
            self.theme_switch.select()

        # --- ссылка
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.grid(row=1, column=0, sticky="ew", pady=8, **pad)
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
        card.grid(row=2, column=0, sticky="ew", pady=8, **pad)
        card.grid_columnconfigure(3, weight=1)

        self.mode = ctk.CTkSegmentedButton(card, values=["Видео", "Аудио"], height=34,
                                           command=lambda _: self._apply_mode())
        self.mode.set(self.settings["mode"])
        self.mode.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="w")

        ctk.CTkLabel(card, text="Качество:").grid(row=0, column=1, padx=(10, 6), pady=(14, 8))
        self.quality = ctk.CTkOptionMenu(card, values=list(VIDEO_QUALITIES), width=130,
                                         command=lambda _: self._remember_quality())
        self.quality.grid(row=0, column=2, pady=(14, 8), sticky="w")

        par = ctk.CTkFrame(card, fg_color="transparent")
        par.grid(row=0, column=3, sticky="e", padx=14, pady=(14, 8))
        ctk.CTkLabel(par, text="Одновременно:").pack(side="left", padx=(0, 6))
        self.parallel = ctk.CTkOptionMenu(par, values=PARALLEL_CHOICES, width=60,
                                          command=lambda _: self._save())
        self.parallel.set(str(self.settings["parallel"]))
        self.parallel.pack(side="left")

        flags = ctk.CTkFrame(card, fg_color="transparent")
        flags.grid(row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 10))
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

        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 14))
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
        qhead.grid(row=3, column=0, sticky="ew", pady=(8, 0), **pad)
        qhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(qhead, text="Очередь",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(qhead, text="Убрать готовые", width=120, height=26,
                      command=self.clear_finished, **outline).grid(row=0, column=1)

        self.queue_frame = ctk.CTkScrollableFrame(self, corner_radius=14)
        self.queue_frame.grid(row=4, column=0, sticky="nsew", pady=6, **pad)
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.queue_frame,
                                        text="Здесь появятся ссылки для скачивания",
                                        text_color=("gray50", "gray50"))
        self.empty_label.grid(row=0, column=0, pady=30)

        # --- общий прогресс
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=5, column=0, sticky="ew", pady=(4, 0), **pad)
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog, height=10, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status = ctk.CTkLabel(prog, text="Готов к работе", anchor="w",
                                   text_color=GRAY_TEXT)
        self.status.grid(row=1, column=0, sticky="ew")

        # --- кнопки
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=6, column=0, sticky="ew", pady=(4, 8), **pad)
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
        self.update_btn = ctk.CTkButton(btns, text="Обновить yt-dlp", width=140, height=42,
                                        corner_radius=10, command=self.update_ytdlp, **outline)
        self.update_btn.grid(row=0, column=4)

        # --- журнал (скрыт по умолчанию)
        self.log_box = ctk.CTkTextbox(self, height=130, corner_radius=10,
                                      font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.configure(state="disabled")
        self.log_visible = False

    def set_main_status(self, text, color=None):
        self.status.configure(text=text, text_color=color or GRAY_TEXT)

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

    # ------------------------------------------------------------ настройки
    def _apply_mode(self):
        if self.mode.get() == "Аудио":
            self.quality.configure(values=list(AUDIO_QUALITIES))
            self.quality.set(self.settings["audio_quality"])
            self.subs_box.configure(state="disabled")
        else:
            self.quality.configure(values=list(VIDEO_QUALITIES))
            self.quality.set(self.settings["video_quality"])
            self.subs_box.configure(state="normal")
        self._save()

    def _remember_quality(self):
        key = "audio_quality" if self.mode.get() == "Аудио" else "video_quality"
        self.settings[key] = self.quality.get()
        self._save()

    def _save(self):
        self.settings.update(mode=self.mode.get(), playlist=self.v_playlist.get(),
                             subs=self.v_subs.get(), embed=self.v_embed.get(),
                             parallel=self.parallel.get())
        save_settings(self.settings)

    def current_opts(self):
        return {"mode": self.mode.get(), "quality": self.quality.get(),
                "playlist": self.v_playlist.get(),
                "subs": self.v_subs.get() and self.mode.get() == "Видео",
                "embed": self.v_embed.get()}

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
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if text.startswith("http") and not self.url_entry.get():
            self.url_entry.insert(0, text)

    def paste_clipboard(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text)

    def add_from_entry(self):
        added = False
        # можно вставить сразу несколько ссылок через пробел или с новой строки
        for url in self.url_entry.get().split():
            if url.startswith("http"):
                self.add_item(url)
                added = True
        if added:
            self.url_entry.delete(0, "end")
        return added

    def add_item(self, url):
        self.empty_label.grid_remove()
        item = QueueItem(self.queue_frame, self, url, self.current_opts())
        item.batch = self.workers_alive > 0
        item.grid(row=len(self.items) + 1, column=0, sticky="ew", pady=4, padx=4)
        with self.lock:
            self.items.append(item)
        # если загрузка уже идёт, а свободные потоки закончились — запускаем ещё один
        if self.workers_alive and self.workers_alive < int(self.parallel.get()):
            self._spawn_worker()

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

    # ------------------------------------------------------------ журнал
    def toggle_log(self):
        if self.log_visible:
            self.log_box.grid_remove()
            self.log_btn.configure(text="Журнал ▾")
        else:
            self.log_box.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 16))
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
        ev(("item_status", item, "Ошибка: " + (last_error[:80] or f"код {code}"), ACCENT))
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
