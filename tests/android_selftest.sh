#!/bin/bash
# Автопроверка Downloader для Android в эмуляторе: ставит APK, скачивает тестовое видео
# с локального сервера (10.0.2.2 — это компьютер сборки) в MP4, MKV и MP3, делает снимки экрана.
set -u
PKG=com.severeclaw.downloader
APK=$1
OUT=$2
mkdir -p "$OUT"
adb install -r -g "$APK" || { echo "FAIL не удалось установить APK" > "$OUT/selftest.log"; exit 1; }
adb shell am start -n $PKG/.MainActivity --es selftest_url http://10.0.2.2:8765/test.mp4
for i in $(seq 1 180); do
  sleep 5
  adb logcat -d -s DLSELFTEST:I -v raw 2>/dev/null | grep -v "^-----" > "$OUT/selftest.log"
  if grep -q "Итог" "$OUT/selftest.log"; then break; fi
  if [ $((i % 6)) -eq 0 ]; then echo "…ждём самопроверку ($((i*5)) с)"; tail -n 2 "$OUT/selftest.log"; fi
done
sleep 2
adb exec-out screencap -p > "$OUT/android-main.png"
adb shell am start -n $PKG/.MainActivity --es tab gallery
sleep 6
adb exec-out screencap -p > "$OUT/android-gallery.png"
adb shell am start -n $PKG/.MainActivity --es tab settings
sleep 3
adb exec-out screencap -p > "$OUT/android-settings.png"
adb shell am start -n $PKG/.MainActivity --ez login true
sleep 12
adb exec-out screencap -p > "$OUT/android-login.png"
adb shell input keyevent KEYCODE_BACK
adb shell ls -la /sdcard/Download/Downloader >> "$OUT/selftest.log" 2>&1
adb logcat -d -t 400 > "$OUT/logcat.txt" 2>&1
cat "$OUT/selftest.log"
if ! grep -q "ВСЁ ХОРОШО" "$OUT/selftest.log"; then
  echo "----- logcat (Downloader) -----"
  grep -iE "downloader|AndroidRuntime|youtubedl" "$OUT/logcat.txt" | tail -n 80
fi
grep -q "ВСЁ ХОРОШО" "$OUT/selftest.log"
