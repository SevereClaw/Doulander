"""Тестовый «ПК» для автопроверки Android: сервер синхронизации из программы
(sync_server.py) с одним файлом и постоянным кодом 424242.
Что пришло с телефона — пишется в файл."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sync_server  # noqa: E402

media, received = sys.argv[1], sys.argv[2]
rec = {"id": "test0001", "url": "", "title": "Видео с ПК", "name": "Видео с ПК.mp4", "ext": "mp4",
       "size": os.path.getsize(media), "date": 1762000000000,
       "opts": {"mode": "video", "quality": "720", "fmt": "mp4"}, "duration": 6, "source": "pc"}


def on_remote(dev, name, items):
    with open(received, "w", encoding="utf-8") as f:
        json.dump({"device": dev, "name": name, "items": items}, f, ensure_ascii=False)


apk = {"tag": "android-v9.9.9-999", "build": 999, "version": "9.9.9",
       "assets": [{"name": "Downloader-x86_64.apk", "size": os.path.getsize(media), "path": media}]}

srv = sync_server.SyncServer("testkey" * 8, lambda: [rec],
                             lambda i: media if i == "test0001" else None, lambda i: None,
                             on_remote, lambda d, n: print("подключён", n, flush=True),
                             name="Тестовый ПК", apk_info=lambda: apk)
srv.code = "424242"
srv.start()
print("sync mock ready", flush=True)
import time  # noqa: E402
while True:
    time.sleep(3600)
