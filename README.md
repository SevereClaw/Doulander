# Doulander (Downloader)

Программа с окном для скачивания видео и музыки с YouTube, VK, TikTok и ещё 1000+ сайтов.
Работает на [yt-dlp](https://github.com/yt-dlp/yt-dlp), интерфейс — [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter).

## Что умеет

- Видео до 4K или MP3 до 320 kbps
- Очередь ссылок, до 5 загрузок одновременно
- Плейлисты, субтитры, обложка и теги в файле
- Своя папка для загрузок, тёмная и светлая тема
- Ctrl+V при любой раскладке, меню по правой кнопке

## Файлы

| Файл | Что это |
|---|---|
| `downloader.py` | Код программы |
| `build.bat` | Сборка у себя на компьютере |
| `.github/workflows/build.yml` | Автосборка на GitHub |

## Как получить готовую программу

### Вариант 1 — собрать на GitHub (Python не нужен)

1. Вкладка **Actions** → **Сборка Downloader** → **Run workflow**.
2. Через 3–5 минут открой запуск → внизу **Artifacts** → скачай **Downloader**.

### Вариант 2 — выпустить новую версию (Release)

1. Поменяй `APP_VERSION` в `downloader.py`, например на `1.0.1`.
2. Закоммить и создай тег:
   ```
   git tag v1.0.1
   git push origin main --tags
   ```
3. GitHub сам соберёт программу и выложит `Downloader.zip` во вкладку **Releases**.

### Вариант 3 — собрать у себя

Нужен Python с галочкой «Add python.exe to PATH». Запусти `build.bat` —
рядом появится папка `Downloader` с `Downloader.exe`, `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe` и `deno.exe`.
