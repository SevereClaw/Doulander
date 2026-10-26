"""
Видеоредактор Downloader: обрезка видео и музыки прямо в программе.

Всё на ffmpeg:
  * предпросмотр — ffmpeg отдаёт кадры (rawvideo) в программу, звук играет ffplay без окна;
  * шкала времени — полоса кадров (ffmpeg tile) и звуковая волна (showwavespic);
  * сохранение — «Быстро» (без перекодирования, по ключевым кадрам)
    или «Точно» (перекодирование H.264 / тот же аудиоформат).
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts", ".wmv")
AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".wma")
MEDIA_EXTS = VIDEO_EXTS + AUDIO_EXTS

AUDIO_CODECS = {        # чем кодировать звук при «Точной» обрезке, по расширению файла
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    ".m4a": ["-c:a", "aac", "-b:a", "256k"],
    ".aac": ["-c:a", "aac", "-b:a", "256k"],
    ".opus": ["-c:a", "libopus", "-b:a", "192k"],
    ".ogg": ["-c:a", "libopus", "-b:a", "192k"],
    ".flac": ["-c:a", "flac"],
    ".wav": ["-c:a", "pcm_s16le"],
}


# ---------------------------------------------------------------- время
def fmt_t(sec, tenths=False):
    sec = max(0.0, float(sec))
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return base + (f".{int((sec % 1) * 10)}" if tenths else "")


def safe_time(sec):
    """Время для имени файла: «1'20» вместо «1:20» (двоеточие в именах Windows нельзя)."""
    return fmt_t(sec).replace(":", "'")


# ---------------------------------------------------------------- сведения о файле
class MediaInfo:
    def __init__(self):
        self.duration = 0.0
        self.width = self.height = 0
        self.fps = 25.0
        self.has_video = False       # настоящее видео (не обложка в mp3)
        self.has_audio = False
        self.has_cover = False


def probe(ctx, path):
    """Длительность, размер кадра, частота кадров, есть ли звук и видео."""
    info = MediaInfo()
    if ctx.ffprobe:
        p = subprocess.run([ctx.ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                            "-show_streams", path], capture_output=True, timeout=60,
                           creationflags=ctx.no_window)
        try:
            data = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            data = {}
        info.duration = float((data.get("format") or {}).get("duration") or 0)
        for st in data.get("streams") or []:
            kind = st.get("codec_type")
            if kind == "audio":
                info.has_audio = True
            elif kind == "video":
                if (st.get("disposition") or {}).get("attached_pic"):
                    info.has_cover = True
                elif not info.has_video:
                    info.has_video = True
                    info.width, info.height = st.get("width") or 0, st.get("height") or 0
                    num, _, den = (st.get("avg_frame_rate") or "25/1").partition("/")
                    try:
                        fps = float(num) / float(den or 1)
                        info.fps = fps if 1 <= fps <= 240 else 25.0
                    except (ValueError, ZeroDivisionError):
                        pass
                    if not info.duration:
                        info.duration = float(st.get("duration") or 0)
        if info.duration:
            return info
    # запасной путь — разбор вывода ffmpeg -i
    p = subprocess.run([ctx.ffmpeg, "-hide_banner", "-i", path], capture_output=True,
                       timeout=60, creationflags=ctx.no_window)
    text = p.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        info.duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in text.splitlines():
        if "Audio:" in line:
            info.has_audio = True
        elif "Video:" in line:
            if "attached pic" in line:
                info.has_cover = True
            elif not info.has_video:
                info.has_video = True
                size = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if size:
                    info.width, info.height = int(size[1]), int(size[2])
                fps = re.search(r"(\d+(?:\.\d+)?) fps", line)
                if fps:
                    info.fps = float(fps[1])
    return info


# ---------------------------------------------------------------- сохранение
def output_path(src, start, end, exact, info):
    folder, name = os.path.split(src)
    stem, ext = os.path.splitext(name)
    if exact and info.has_video and ext.lower() not in (".mp4", ".mkv"):
        ext = ".mp4"            # перекодируем в H.264 — он помещается в mp4
    base = f"{stem} (обрезка {safe_time(start)}–{safe_time(end)})"
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


def trim_command(ctx, src, dst, start, end, exact, with_audio, info):
    dur = max(0.05, end - start)
    cmd = [ctx.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
           "-progress", "pipe:1", "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}"]
    ext = os.path.splitext(dst)[1].lower()
    if not exact:
        cmd += ["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero"]
        if not with_audio:
            cmd += ["-an"]
    else:
        if info.has_video:
            cmd += ["-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p"]
        elif info.has_cover:
            cmd += ["-map", "0:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        if with_audio and info.has_audio:
            cmd += ["-map", "0:a:0"] + AUDIO_CODECS.get(ext, ["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd += ["-an"]
        if ext == ".mp4":
            cmd += ["-movflags", "+faststart"]
    cmd += ["-map_metadata", "0", dst]
    return cmd


def run_trim(ctx, src, dst, start, end, exact, with_audio, info, progress=None, stop=None):
    """Запускает ffmpeg и сообщает прогресс 0..1. Возвращает (ok, текст ошибки)."""
    cmd = trim_command(ctx, src, dst, start, end, exact, with_audio, info)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, creationflags=ctx.no_window)
    total = max(0.05, end - start)
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                done = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            if progress:
                progress(min(1.0, done / total))
        if stop and stop():
            proc.kill()
            break
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    code = proc.wait()
    ok = code == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0
    if not ok and os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            pass
    return ok, err[-300:]


# ---------------------------------------------------------------- картинки для шкалы
def make_filmstrip(ctx, path, info, count=16, height=56):
    """Полоса из `count` кадров по всей длине видео."""
    if not info.has_video or info.duration <= 0:
        return None
    w = max(8, int(height * (info.width / info.height))) if info.height else int(height * 16 / 9)
    fps = count / max(info.duration, 0.1)
    p = subprocess.run([ctx.ffmpeg, "-v", "error", "-i", path, "-vf",
                        f"fps={fps:.6f},scale={w}:{height},tile={count}x1", "-frames:v", "1",
                        "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True, timeout=120, creationflags=ctx.no_window)
    if p.returncode != 0 or not p.stdout:
        return None
    from io import BytesIO
    return Image.open(BytesIO(p.stdout)).convert("RGB")


def make_waveform(ctx, path, info, width=1200, height=40, color="#FFFFFF"):
    if not info.has_audio:
        return None
    p = subprocess.run([ctx.ffmpeg, "-v", "error", "-i", path, "-filter_complex",
                        f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}"
                        f":colors={color}", "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"],
                       capture_output=True, timeout=120, creationflags=ctx.no_window)
    if p.returncode != 0 or not p.stdout:
        return None
    from io import BytesIO
    return Image.open(BytesIO(p.stdout)).convert("RGBA")


def grab_frame(ctx, path, t, w, h, cover=False):
    """Один кадр (или обложка mp3) как картинка PIL."""
    if cover:
        cmd = [ctx.ffmpeg, "-v", "error", "-i", path, "-an", "-frames:v", "1"]
    else:
        cmd = [ctx.ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
               "-frames:v", "1"]
    cmd += ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                   f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=ctx.no_window)
    if len(p.stdout) < w * h * 3:
        return None
    return Image.frombytes("RGB", (w, h), p.stdout[:w * h * 3])


# ---------------------------------------------------------------- проигрыватель
class Player:
    """Кадры — из ffmpeg (rawvideo), звук — ffplay без окна.
    Часы воспроизведения запускаются, когда пришёл первый кадр, — тогда же стартует звук.
    Так картинка и звук совпадают, сколько бы ни запускался ffmpeg на этом компьютере."""

    def __init__(self, ctx, path, info, w, h, on_frame, on_end):
        self.ctx, self.path, self.info = ctx, path, info
        self.w, self.h = w, h
        self.on_frame, self.on_end = on_frame, on_end
        self.vproc = self.aproc = None
        self.playing = False
        self.pos0 = 0.0
        self.wall0 = 0.0
        self.end = 0.0
        self._gen = 0
        self.frames_read = 0      # для диагностики
        self.frames_sent = 0
        self.error = None

    def position(self):
        if not self.playing or self.wall0 is None:
            return self.pos0
        return min(self.end, self.pos0 + max(0.0, time.time() - self.wall0))

    def play(self, start, end, with_audio=True):
        self.stop()
        self._gen += 1
        gen = self._gen
        self.pos0, self.end = start, end
        dur = max(0.05, end - start)
        self.playing = True
        self.wall0 = None                          # часы пойдут с первым кадром
        self.with_audio = with_audio
        nw = self.ctx.no_window
        if self.info.has_video:
            fps = min(self.info.fps, 30.0)
            self.vproc = subprocess.Popen(
                [self.ctx.ffmpeg, "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-i", self.path,
                 "-t", f"{dur:.3f}", "-an", "-vf",
                 f"fps={fps:.3f},scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,"
                 f"pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2:color=black",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=nw)
            threading.Thread(target=self._read_video, args=(gen, fps), daemon=True).start()
        else:
            self._start_clock()                    # только звук — часы сразу
        threading.Thread(target=self._watch_end, args=(gen,), daemon=True).start()

    def _start_clock(self):
        """Пошли часы — запускаем и звук с того же места."""
        self.wall0 = time.time()
        if self.with_audio and self.info.has_audio and self.ctx.ffplay:
            self.aproc = subprocess.Popen(
                [self.ctx.ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-ss", f"{self.pos0:.3f}", "-t", f"{max(0.05, self.end - self.pos0):.3f}",
                 self.path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=self.ctx.no_window)

    def _read_video(self, gen, fps):
        try:
            self._read_video_loop(gen, fps)
        except Exception as e:      # noqa: BLE001 — покажем в окне и в самопроверке
            self.error = repr(e)

    def _read_video_loop(self, gen, fps):
        size = self.w * self.h * 3
        i = 0
        proc = self.vproc
        while gen == self._gen and proc and proc.stdout:
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                if proc.poll() not in (None, 0) and not self.error:
                    self.error = f"ffmpeg завершился с кодом {proc.returncode}"
                break
            self.frames_read += 1
            if gen != self._gen:
                break
            if self.wall0 is None:                      # первый кадр — пускаем часы и звук
                self._start_clock()
            target = self.wall0 + i / fps
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
            # опаздываем больше чем на четверть секунды — кадр пропускаем, иначе показываем
            if delay > -0.25 and gen == self._gen:
                self.on_frame(buf)
                self.frames_sent += 1
            i += 1

    def _watch_end(self, gen):
        while gen == self._gen and self.playing:
            if self.wall0 is not None and time.time() - self.wall0 >= self.end - self.pos0:
                if gen == self._gen:
                    self.playing = False
                    self.pos0 = self.end
                    self.on_end()
                return
            time.sleep(0.02)

    def stop(self):
        if self.playing:
            self.pos0 = self.position()
        self.playing = False
        self._gen += 1
        for p in (self.vproc, self.aproc):
            if p and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        self.vproc = self.aproc = None


# ---------------------------------------------------------------- окно редактора
class VideoEditor(ctk.CTkToplevel):
    PREVIEW_W, PREVIEW_H = 800, 450
    STRIP_H, WAVE_H, RULER_H = 56, 26, 22

    def __init__(self, parent, ctx, path):
        super().__init__(parent)
        self.ctx, self.path = ctx, path
        self.title("Редактор — " + os.path.basename(path))
        self.geometry("880x820")
        self.minsize(720, 640)
        if ctx.icon and os.path.isfile(ctx.icon):
            self.after(250, lambda: self.iconbitmap(ctx.icon))
        self.info = MediaInfo()
        self.start_t = self.end_t = 0.0
        self.playhead = 0.0
        self.with_audio = True
        self.exact = ctk.StringVar(value="Точно")
        self.frames_shown = 0
        self._latest = None
        self._frame_lock = threading.Lock()
        self._scrub_req = None
        self._scrub_busy = False
        self._drag = None
        self._strip_img = None          # кадры + волна на всю ширину (PIL)
        self._strip_tk = None
        self._film = self._wave = None
        self._exporting = False
        self._export_stop = False
        self.player = None
        self._calls = queue.Queue()      # поручения окну из фоновых потоков
        self._build()
        self.bind("<Destroy>", self._on_destroy)
        self.after(150, self.lift)
        self.set_status("Загружаю видео…")
        threading.Thread(target=self._load, daemon=True).start()
        self.after(15, self._tick)

    # ---------- вёрстка
    def _build(self):
        c = self.ctx
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        head.grid_columnconfigure(1, weight=1)
        c.title_widget(head, "Редактор", 15).grid(row=0, column=0, sticky="w")
        name = os.path.basename(self.path)
        ctk.CTkLabel(head, text=name if len(name) < 70 else name[:67] + "…", anchor="w",
                     text_color=c.gray).grid(row=0, column=1, sticky="ew", padx=12)

        # предпросмотр
        self.preview = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                 width=self.PREVIEW_W, height=self.PREVIEW_H)
        self.preview.grid(row=1, column=0, padx=16, sticky="n")
        self._img_item = self.preview.create_image(self.PREVIEW_W // 2, self.PREVIEW_H // 2)
        self._overlay_bg = self.preview.create_rectangle(8, self.PREVIEW_H - 32, 96,
                                                         self.PREVIEW_H - 6, fill="#000000",
                                                         outline="")
        self._overlay = self.preview.create_text(16, self.PREVIEW_H - 19, anchor="w",
                                                 fill="#FFFFFF", text="",
                                                 font=("Bahnschrift", 13, "bold"))
        self.preview.bind("<Button-1>", lambda e: self.toggle_play())

        # шкала
        self.timeline = tk.Canvas(self, height=self.RULER_H + self.STRIP_H + self.WAVE_H + 10,
                                  bg=c.timeline_bg, highlightthickness=0)
        self.timeline.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 4))
        self.timeline.bind("<Configure>", lambda e: self._render_strip())
        self.timeline.bind("<ButtonPress-1>", self._on_press)
        self.timeline.bind("<B1-Motion>", self._on_drag)
        self.timeline.bind("<ButtonRelease-1>", self._on_release)

        # управление
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 2))
        bar.grid_columnconfigure(3, weight=1)
        self.play_btn = ctk.CTkButton(bar, text="▶", width=46, height=38, command=self.toggle_play,
                                      font=ctk.CTkFont(size=16, weight="bold"))
        self.play_btn.grid(row=0, column=0)
        self.time_lbl = ctk.CTkLabel(bar, text="0:00 / 0:00", width=120,
                                     font=ctk.CTkFont(size=14, weight="bold"))
        self.time_lbl.grid(row=0, column=1, padx=(10, 4))
        self.sel_lbl = ctk.CTkLabel(bar, text="", text_color=c.gray)
        self.sel_lbl.grid(row=0, column=2, padx=6)
        self.audio_btn = ctk.CTkButton(bar, text="🔊 Звук", width=100, height=34,
                                       command=self.toggle_audio, **c.btn2)
        self.audio_btn.grid(row=0, column=4, padx=(6, 0))
        ctk.CTkButton(bar, text="Сбросить", width=90, height=34, command=self.reset_selection,
                      **c.btn2).grid(row=0, column=5, padx=(6, 0))

        save = ctk.CTkFrame(self, fg_color="transparent")
        save.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 2))
        save.grid_columnconfigure(2, weight=1)
        ctk.CTkSegmentedButton(save, values=["Быстро", "Точно"], variable=self.exact,
                               command=lambda _: self._update_labels()).grid(row=0, column=0)
        self.mode_hint = ctk.CTkLabel(save, text="", text_color=c.gray, anchor="w")
        self.mode_hint.grid(row=0, column=1, columnspan=2, sticky="ew", padx=10)
        self.save_btn = ctk.CTkButton(save, text="✂  Сохранить копию", width=170, height=40,
                                      fg_color=c.accent, hover_color=c.accent_hover,
                                      font=ctk.CTkFont(size=14, weight="bold"),
                                      command=self.save)
        self.save_btn.grid(row=0, column=3)

        self.progress = ctk.CTkProgressBar(self, height=8, progress_color=c.accent)
        self.progress.set(0)
        self.progress.grid(row=5, column=0, sticky="ew", padx=16, pady=(8, 0))
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=6, column=0, sticky="ew", padx=16, pady=(2, 12))
        bottom.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(bottom, text="", anchor="w", text_color=c.gray)
        self.status.grid(row=0, column=0, sticky="ew")
        self.open_btn = ctk.CTkButton(bottom, text="▶ Открыть", width=100, height=30,
                                      command=self._open_result, **c.btn2)
        self.folder_btn = ctk.CTkButton(bottom, text="📂 В папке", width=100, height=30,
                                        command=self._show_result, **c.btn2)
        self.result = None
        ctk.CTkLabel(self, text="Пробел — пуск/пауза   ←/→ — 1 с (Shift — 5 с)   "
                                "I / O — начало / конец здесь   Enter — сохранить",
                     text_color=c.gray, font=ctk.CTkFont(size=11)).grid(
            row=7, column=0, pady=(0, 8))

        for seq, fn in (("<space>", lambda e: self.toggle_play()),
                        ("<Left>", lambda e: self.step(-1)),
                        ("<Right>", lambda e: self.step(1)),
                        ("<Shift-Left>", lambda e: self.step(-5)),
                        ("<Shift-Right>", lambda e: self.step(5)),
                        ("<Home>", lambda e: self.seek(self.start_t)),
                        ("<End>", lambda e: self.seek(self.end_t)),
                        ("<Return>", lambda e: self.save()),
                        ("<KeyPress>", self._on_key)):
            self.bind(seq, fn)

    def _ui(self, fn):
        """Выполнить в окне (из любого потока) — заберёт главный цикл _tick."""
        self._calls.put(fn)

    def set_status(self, text, color=None):
        if self.winfo_exists():
            self.status.configure(text=text, text_color=color or self.ctx.gray)

    # ---------- загрузка
    def _load(self):
        try:
            self.info = probe(self.ctx, self.path)
        except Exception as e:       # noqa: BLE001 — показываем пользователю
            self._ui(lambda: self.set_status(f"Не удалось открыть файл: {e}", self.ctx.accent))
            return
        if self.info.duration <= 0:
            self._ui(lambda: self.set_status("Не удалось узнать длительность файла",
                                                  self.ctx.accent))
            return
        self.start_t, self.end_t = 0.0, self.info.duration
        self._ui(self._loaded)
        film = make_filmstrip(self.ctx, self.path, self.info)
        wave = make_waveform(self.ctx, self.path, self.info, color=self.ctx.wave_color)
        first = grab_frame(self.ctx, self.path, 0, self.PREVIEW_W, self.PREVIEW_H,
                           cover=not self.info.has_video)
        self._film, self._wave = film, wave
        self._ui(self._render_strip)
        if first is not None:
            self._show_image(first)
        elif not self.info.has_video:
            self._ui(self._show_audio_placeholder)

    def _loaded(self):
        if not self.winfo_exists():
            return
        self._update_labels()
        hint = ""
        if self.info.has_audio and not self.ctx.ffplay:
            hint = "   Звук в предпросмотре недоступен: нет ffplay.exe (⚙ → Обновить компоненты)"
        self.set_status(("Видео" if self.info.has_video else "Аудио") +
                        f": {fmt_t(self.info.duration)}" +
                        (f", {self.info.width}×{self.info.height}" if self.info.has_video else "") +
                        hint)
        self.player = Player(self.ctx, self.path, self.info, self.PREVIEW_W, self.PREVIEW_H,
                             self._on_frame, self._on_play_end)

    def _show_audio_placeholder(self):
        img = Image.new("RGB", (self.PREVIEW_W, self.PREVIEW_H), "#000000")
        d = ImageDraw.Draw(img)
        d.text((self.PREVIEW_W // 2 - 20, self.PREVIEW_H // 2 - 20), "♪", fill="#FFFFFF")
        self._show_image(img)

    def _show_image(self, img):
        with self._frame_lock:
            self._latest = img

    # ---------- кадры из проигрывателя (другой поток)
    def _on_frame(self, buf):
        with self._frame_lock:
            self._latest = buf

    def _on_play_end(self):
        self._ui(self._stopped_at_end)

    def _stopped_at_end(self):
        if self.winfo_exists():
            self.playhead = self.end_t
            self.play_btn.configure(text="▶")

    def _tick(self):
        """Главный цикл: показать новый кадр, подвинуть бегунок и время."""
        if not self.winfo_exists():
            return
        while True:                     # поручения из фоновых потоков
            try:
                fn = self._calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except tk.TclError:
                pass
        with self._frame_lock:
            frame, self._latest = self._latest, None
        if frame is not None:
            img = frame if isinstance(frame, Image.Image) else \
                Image.frombytes("RGB", (self.PREVIEW_W, self.PREVIEW_H), frame)
            self._tk_frame = ImageTk.PhotoImage(img)
            self.preview.itemconfigure(self._img_item, image=self._tk_frame)
            self.preview.tag_raise(self._overlay_bg)
            self.preview.tag_raise(self._overlay)
            self.frames_shown += 1
        if self.player and self.player.playing:
            self.playhead = self.player.position()
            self._move_head()
        text = fmt_t(self.playhead, tenths=True)
        if text != getattr(self, "_last_time_text", None):
            self._last_time_text = text
            self.time_lbl.configure(text=f"{fmt_t(self.playhead)} / {fmt_t(self.info.duration)}")
            self.preview.itemconfigure(self._overlay, text=text)
        self.after(15, self._tick)

    # ---------- шкала
    def _x(self, t):
        w = self.timeline.winfo_width() - 24
        return 12 + (t / self.info.duration) * w if self.info.duration else 12

    def _t(self, x):
        w = max(1, self.timeline.winfo_width() - 24)
        return min(self.info.duration, max(0.0, (x - 12) / w * self.info.duration))

    def _render_strip(self):
        """Полоса кадров и волна под ширину шкалы (без затемнения)."""
        if not self.winfo_exists():
            return
        w = max(50, self.timeline.winfo_width() - 24)
        strip = Image.new("RGB", (w, self.STRIP_H + self.WAVE_H), self.ctx.timeline_bg)
        if self._film is not None:
            strip.paste(self._film.resize((w, self.STRIP_H), Image.LANCZOS), (0, 0))
        elif self.info.has_audio:
            ImageDraw.Draw(strip).rectangle((0, 0, w, self.STRIP_H), fill="#1A1A1A")
        if self._wave is not None:
            wave = self._wave.resize((w, self.WAVE_H if self._film is not None else
                                      self.STRIP_H + self.WAVE_H), Image.LANCZOS)
            y = self.STRIP_H if self._film is not None else 0
            strip.paste(wave, (0, y), wave)
        self._strip_img = strip
        self._draw_overlays()

    def _draw_overlays(self):
        cv = self.timeline
        cv.delete("all")
        if not self.info.duration:
            return
        width = cv.winfo_width()
        top = self.RULER_H
        bottom = top + self.STRIP_H + self.WAVE_H
        # линейка
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)
                     if self.info.duration / s <= 8), 3600)
        t = 0.0
        while t <= self.info.duration + 0.001:
            x = self._x(t)
            cv.create_line(x, top - 6, x, top - 1, fill=self.ctx.ruler)
            cv.create_text(x, 2, anchor="n", text=fmt_t(t), fill=self.ctx.ruler,
                           font=("Bahnschrift", 9))
            t += step
        # полоса с затемнением вне выделения
        if self._strip_img is not None:
            img = self._strip_img.copy()
            x0 = int(self._x(self.start_t)) - 12
            x1 = int(self._x(self.end_t)) - 12
            dark = Image.new("RGB", img.size, "#000000")
            mask = Image.new("L", img.size, 150)
            ImageDraw.Draw(mask).rectangle((x0, 0, x1, img.height), fill=0)
            img.paste(dark, (0, 0), mask)
            self._strip_tk = ImageTk.PhotoImage(img)
            cv.create_image(12, top, anchor="nw", image=self._strip_tk)
        # рамка выделения и ручки
        xs, xe = self._x(self.start_t), self._x(self.end_t)
        frame = self.ctx.handle
        cv.create_rectangle(xs, top - 1, xe, bottom + 1, outline=frame, width=3)
        for x in (xs, xe):
            cv.create_rectangle(x - 6, top - 3, x + 6, bottom + 3, fill=frame, outline="")
            cv.create_line(x - 1, top + 18, x - 1, bottom - 18, fill="#000000", width=1)
            cv.create_line(x + 1, top + 18, x + 1, bottom - 18, fill="#000000", width=1)
        # бегунок
        xp = self._x(self.playhead)
        cv.create_line(xp, top - 8, xp, bottom + 4, fill=self.ctx.playhead, width=2,
                       tags=("head", "head_line"))
        cv.create_polygon(xp - 6, top - 12, xp + 6, top - 12, xp, top - 4,
                          fill=self.ctx.playhead, outline="", tags=("head", "head_tri"))

    def _move_head(self):
        """Во время воспроизведения двигаем только бегунок (без перерисовки полосы)."""
        cv = self.timeline
        top = self.RULER_H
        bottom = top + self.STRIP_H + self.WAVE_H
        xp = self._x(self.playhead)
        cv.coords("head_line", xp, top - 8, xp, bottom + 4)
        cv.coords("head_tri", xp - 6, top - 12, xp + 6, top - 12, xp, top - 4)

    # ---------- мышь
    def _on_press(self, e):
        if not self.info.duration:
            return
        xs, xe = self._x(self.start_t), self._x(self.end_t)
        if abs(e.x - xs) <= 10:
            self._drag = "start"
        elif abs(e.x - xe) <= 10:
            self._drag = "end"
        else:
            self._drag = "head"
            self.seek(self._t(e.x))

    def _on_drag(self, e):
        if not self._drag or not self.info.duration:
            return
        t = self._t(e.x)
        min_len = min(0.5, self.info.duration / 2)
        if self._drag == "start":
            self.start_t = min(t, self.end_t - min_len)
            self.seek(self.start_t, quiet=True)
        elif self._drag == "end":
            self.end_t = max(t, self.start_t + min_len)
            self.seek(self.end_t, quiet=True)
        else:
            self.seek(t, quiet=True)
        self._update_labels()

    def _on_release(self, e):
        self._drag = None
        self._request_frame(self.playhead)

    # ---------- клавиши
    def _on_key(self, e):
        # I/O по коду клавиши — работает и на русской раскладке (Ш/Щ)
        if e.keycode == 73 or e.keysym.lower() == "i":
            self.mark_start()
        elif e.keycode == 79 or e.keysym.lower() == "o":
            self.mark_end()

    def mark_start(self):
        self.start_t = min(self.playhead, self.end_t - 0.2)
        self._update_labels()
        self._draw_overlays()

    def mark_end(self):
        self.end_t = max(self.playhead, self.start_t + 0.2)
        self._update_labels()
        self._draw_overlays()

    def reset_selection(self):
        self.start_t, self.end_t = 0.0, self.info.duration
        self._update_labels()
        self._draw_overlays()

    def step(self, sec):
        self.seek(self.playhead + sec)

    # ---------- воспроизведение и перемотка
    def toggle_play(self):
        if not self.player:
            return
        if self.player.playing:
            self.player.stop()
            self.playhead = self.player.pos0
            self.play_btn.configure(text="▶")
            self._request_frame(self.playhead)
            return
        start = self.playhead
        if start < self.start_t or start >= self.end_t - 0.05:
            start = self.start_t                 # с начала выделения
        self.playhead = start
        self.player.play(start, self.end_t, self.with_audio)
        self.play_btn.configure(text="⏸")

    def seek(self, t, quiet=False):
        if not self.info.duration:
            return
        was_playing = self.player and self.player.playing
        if was_playing:
            self.player.stop()
            self.play_btn.configure(text="▶")
        self.playhead = min(self.info.duration, max(0.0, t))
        if self.player:
            self.player.pos0 = self.playhead
        self._draw_overlays()
        self._request_frame(self.playhead, fast=quiet)

    def _request_frame(self, t, fast=False):
        if not self.info.has_video:
            return
        self._scrub_req = t
        if not self._scrub_busy:
            self._scrub_busy = True
            threading.Thread(target=self._scrub_worker, daemon=True).start()

    def _scrub_worker(self):
        """Показывает кадр под бегунком; если пока грузили, бегунок уехал — берём новый."""
        try:
            while self._scrub_req is not None:
                t, self._scrub_req = self._scrub_req, None
                img = grab_frame(self.ctx, self.path, t, self.PREVIEW_W, self.PREVIEW_H)
                if img is not None and not (self.player and self.player.playing):
                    self._show_image(img)
        finally:
            self._scrub_busy = False

    def toggle_audio(self):
        self.with_audio = not self.with_audio
        self.audio_btn.configure(text="🔊 Звук" if self.with_audio else "🔇 Без звука")
        self._update_labels()
        if self.player and self.player.playing:      # перезапустить с новым звуком
            pos = self.player.position()
            self.player.play(pos, self.end_t, self.with_audio)

    def _update_labels(self):
        length = self.end_t - self.start_t
        self.sel_lbl.configure(text=f"Выделено: {fmt_t(self.start_t, True)} — "
                                    f"{fmt_t(self.end_t, True)}   ({fmt_t(length, True)})")
        if self.exact.get() == "Быстро":
            hint = "Мгновенно и без потери качества; границы — по ближайшим ключевым кадрам"
        else:
            hint = "Ровно по выделению; файл перекодируется (дольше)"
        if not self.with_audio:
            hint += " · без звука"
        self.mode_hint.configure(text=hint)

    # ---------- сохранение
    def save(self):
        if self._exporting or not self.info.duration:
            return
        if self.player and self.player.playing:
            self.toggle_play()
        exact = self.exact.get() == "Точно"
        dst = output_path(self.path, self.start_t, self.end_t, exact, self.info)
        self._exporting = True
        self.save_btn.configure(state="disabled", text="Сохраняю…")
        self.open_btn.grid_remove()
        self.folder_btn.grid_remove()
        self.progress.set(0)
        self.set_status("Сохраняю " + os.path.basename(dst) + "…")
        start, end, audio = self.start_t, self.end_t, self.with_audio

        def run():
            ok, err = run_trim(self.ctx, self.path, dst, start, end, exact, audio, self.info,
                               progress=lambda f: self._ui(lambda: self.progress.set(f)),
                               stop=lambda: self._export_stop)
            self._ui(lambda: self._saved(ok, err, dst))
        threading.Thread(target=run, daemon=True).start()

    def _saved(self, ok, err, dst):
        self._exporting = False
        if not self.winfo_exists():
            return
        self.save_btn.configure(state="normal", text="✂  Сохранить копию")
        if ok:
            self.progress.set(1)
            self.result = dst
            self.set_status("✓ Сохранено: " + os.path.basename(dst), self.ctx.green)
            self.open_btn.grid(row=0, column=1, padx=(6, 0))
            self.folder_btn.grid(row=0, column=2, padx=(6, 0))
            if self.ctx.on_saved:
                self.ctx.on_saved(dst)
        else:
            self.progress.set(0)
            self.set_status("Не удалось сохранить: " + (err or "ошибка ffmpeg"), self.ctx.accent)

    def _open_result(self):
        if self.result and os.path.exists(self.result):
            os.startfile(self.result)

    def _show_result(self):
        if self.result:
            self.ctx.show_in_folder(self.result)

    def _on_destroy(self, e):
        if e.widget is self:
            self._export_stop = True
            if self.player:
                self.player.stop()
