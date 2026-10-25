@echo off
title Сборка Downloader
chcp 65001 >nul
cd /d "%~dp0"

:: Готовая программа соберётся в папку "Downloader" рядом с этим файлом
set "OUT=%~dp0Downloader"
set "TMPD=%TEMP%\downloader_build"

set "URL_YTDLP=https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
set "URL_FFMPEG=https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
set "URL_DENO=https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

if not exist "downloader.py" (
    echo Не найден downloader.py — положите build.bat в одну папку с ним.
    goto fail
)
if not exist "%OUT%" mkdir "%OUT%"
if exist "%TMPD%" rmdir /s /q "%TMPD%"
mkdir "%TMPD%"

:: ------------------------------------------------ Python
set "PY="
py --version >nul 2>nul && set "PY=py"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python не найден.
    echo Установите его с https://www.python.org/downloads/
    echo и при установке отметьте галочку "Add python.exe to PATH".
    goto fail
)

echo.
echo [1/5] Устанавливаю библиотеки Python...
%PY% -m pip install --upgrade customtkinter pyinstaller
if errorlevel 1 goto fail

echo.
echo [2/5] Собираю Downloader.exe (минута-две)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Downloader --collect-data customtkinter --distpath "%TMPD%\dist" --workpath "%TMPD%\build" --specpath "%TMPD%" downloader.py
if errorlevel 1 goto fail
copy /y "%TMPD%\dist\Downloader.exe" "%OUT%\Downloader.exe" >nul
if errorlevel 1 goto fail

:: ------------------------------------------------ yt-dlp
echo.
echo [3/5] yt-dlp
if exist "%OUT%\yt-dlp.exe" goto ytdlp_ok
call :download "%URL_YTDLP%" "%OUT%\yt-dlp.exe" || goto fail
goto ffmpeg
:ytdlp_ok
echo   Уже есть — пропускаю (обновить можно кнопкой в программе)

:: ------------------------------------------------ ffmpeg
:ffmpeg
echo.
echo [4/5] ffmpeg
if exist "%OUT%\ffmpeg.exe" if exist "%OUT%\ffprobe.exe" goto ffmpeg_ok
call :download "%URL_FFMPEG%" "%TMPD%\ffmpeg.zip" || goto fail
echo   Распаковываю...
call :unzip "%TMPD%\ffmpeg.zip" "%TMPD%\ffmpeg" || goto fail
for /r "%TMPD%\ffmpeg" %%F in (ffmpeg.exe ffprobe.exe) do if exist "%%F" copy /y "%%F" "%OUT%\" >nul
if not exist "%OUT%\ffmpeg.exe" goto fail
goto deno
:ffmpeg_ok
echo   Уже есть — пропускаю

:: ------------------------------------------------ deno
:deno
echo.
echo [5/5] deno
if exist "%OUT%\deno.exe" goto deno_ok
call :download "%URL_DENO%" "%TMPD%\deno.zip" || goto fail
echo   Распаковываю...
call :unzip "%TMPD%\deno.zip" "%TMPD%\deno" || goto fail
copy /y "%TMPD%\deno\deno.exe" "%OUT%\deno.exe" >nul
if errorlevel 1 goto fail
goto done
:deno_ok
echo   Уже есть — пропускаю

:: ------------------------------------------------ готово
:done
rmdir /s /q "%TMPD%" >nul 2>nul
echo.
echo ==========================================
echo  Готово! Всё лежит в папке Downloader:
echo ==========================================
dir /b "%OUT%\*.exe"
echo.
echo Запускайте Downloader.exe. Папку можно переносить куда угодно целиком.
start "" explorer "%OUT%"
pause
exit /b 0

:fail
echo.
echo Что-то пошло не так — посмотрите сообщение выше.
echo Если ошибка при скачивании — проверьте интернет и запустите build.bat ещё раз.
pause
exit /b 1

:: ================================================ подпрограммы
:: call :download "URL" "куда сохранить"
:download
echo   Скачиваю %~nx2...
set "DL_URL=%~1"
set "DL_OUT=%~2"
curl.exe -L --fail --progress-bar -o "%~2" "%~1" && exit /b 0
echo   curl не сработал, пробую через PowerShell...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing -Uri $env:DL_URL -OutFile $env:DL_OUT" && exit /b 0
if exist "%~2" del /q "%~2"
echo   Не удалось скачать %~1
exit /b 1

:: call :unzip "архив.zip" "куда распаковать"
:unzip
set "ZIP_IN=%~1"
set "ZIP_OUT=%~2"
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath $env:ZIP_IN -DestinationPath $env:ZIP_OUT"
exit /b %errorlevel%
