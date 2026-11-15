# -*- coding: utf-8 -*-
"""Лишние картинки-обложки после неудачных роликов убираются, готовые файлы и чужие картинки — нет."""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(HERE, "downloader.py"), encoding="utf-8").read()
a = src.index("THUMB_LINE = ")
b = src.index("def tidy_lyrics(files):")
ns = {"re": re, "os": os}
exec(src[a:b], ns)                                   # только эти функции — без окна и библиотек программы
thumbnail_written, remove_leftover_thumbnails = ns["thumbnail_written"], ns["remove_leftover_thumbnails"]

fails = []


def check(name, ok, info=""):
    print(("OK  " if ok else "FAIL"), name, info)
    if not ok:
        fails.append(name)


d = tempfile.mkdtemp()


def touch(name):
    p = os.path.join(d, name)
    open(p, "w").close()
    return p


line = "[info] Writing video thumbnail 41 to: " + os.path.join(d, "Автор - Topic - 遺言状.webp")
check("строка журнала разбирается", thumbnail_written(line) == os.path.join(d, "Автор - Topic - 遺言状.webp"))
check("обычная строка — не обложка", thumbnail_written("[download] 100% of 3.2MiB") is None
      and thumbnail_written("[info] Downloading video thumbnail 41 ...") is None)

orphan = touch("Автор - Topic - 遺言状.jpg")            # ролик не скачался, картинку успели переделать в jpg
ok_mp3, ok_jpg_gone = touch("Готовый трек.mp3"), os.path.join(d, "Готовый трек.jpg")   # вшита и удалена yt-dlp
still = touch("Ещё один.png")                         # осталась от ролика с ошибкой
mine = touch("cover.jpg")                             # своя картинка пользователя — не из этого запуска
other = touch("Чужой трек.jpg")
thumbs = [os.path.join(d, "Автор - Topic - 遺言状.webp"), os.path.join(d, "Готовый трек.webp"), "Ещё один.webp"]
n = remove_leftover_thumbnails(thumbs, d, keep=[ok_mp3])
check("убрано две лишние картинки", n == 2, n)
check("обложка несостоявшегося ролика убрана", not os.path.exists(orphan) and not os.path.exists(still))
check("готовый трек на месте", os.path.isfile(ok_mp3) and not os.path.exists(ok_jpg_gone))
check("чужие картинки не тронуты", os.path.isfile(mine) and os.path.isfile(other))
pic = touch("Картинка.jpg")                            # скачивали саму картинку — это готовый файл
check("готовый файл-картинку не удаляем", remove_leftover_thumbnails([pic], d, keep=[pic]) == 0 and os.path.isfile(pic))
check("повторный вызов ничего не ломает", remove_leftover_thumbnails(thumbs, d) == 0)

print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
