@echo off
title Обновление Downloader
chcp 65001 >nul
cd /d "%~dp0"

set "REPO=SevereClaw/Doulander"
set "TMPD=%TEMP%\downloader_update"
set "INFO=%~dp0update_info.txt"

echo ==========================================
echo      ОБНОВЛЕНИЕ DOWNLOADER
echo ==========================================
echo.

:: ------------------------------------------------ 1. GitHub CLI
set "GH="
where gh >nul 2>nul && set "GH=gh"
if not defined GH if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "GH=%ProgramFiles%\GitHub CLI\gh.exe"
if defined GH goto gh_ok

echo Для обновления нужна программа GitHub CLI (gh) — репозиторий закрытый.
choice /c YN /m "Установить её сейчас через winget"
if errorlevel 2 goto fail
winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "GH=%ProgramFiles%\GitHub CLI\gh.exe"
if not defined GH (
    echo Не получилось установить автоматически.
    echo Скачайте вручную: https://cli.github.com/ и запустите update.bat снова.
    goto fail
)
:gh_ok

:: ------------------------------------------------ 2. Вход в GitHub (один раз)
"%GH%" auth status >nul 2>nul && goto auth_ok
echo.
echo Нужно один раз войти в GitHub.
echo Откроется браузер — подтвердите вход кодом, который покажется ниже.
echo.
"%GH%" auth login --hostname github.com --git-protocol https --web
"%GH%" auth status >nul 2>nul || goto fail
:auth_ok

:: ------------------------------------------------ 3. Последняя удачная сборка
echo Проверяю последнюю сборку на GitHub...
set "RUN="
set "WHEN="
for /f "tokens=1,* delims= " %%a in ('""%GH%" run list --repo %REPO% --workflow build.yml --status success --limit 1 --json databaseId,createdAt --template "{{range .}}{{.databaseId}} {{timeago .createdAt}}{{end}}""') do (
    set "RUN=%%a"
    set "WHEN=%%b"
)
if not defined RUN (
    echo Не найдено ни одной удачной сборки в %REPO%.
    echo Запустите сборку: вкладка Actions - Сборка Downloader - Run workflow.
    goto fail
)
echo   Сборка №%RUN%, собрана %WHEN%

set "HAVE="
if exist "%INFO%" set /p HAVE=<"%INFO%"
if not exist "%~dp0Downloader.exe" set "HAVE="
if "%HAVE%"=="%RUN%" (
    echo.
    echo У вас уже последняя версия.
    goto tools
)

:: ------------------------------------------------ 4. Скачивание
echo.
echo Скачиваю новую версию...
if exist "%TMPD%" rmdir /s /q "%TMPD%"
mkdir "%TMPD%"
"%GH%" run download %RUN% --repo %REPO% --name Downloader-app --dir "%TMPD%"
if errorlevel 1 goto fail
if not exist "%TMPD%\Downloader.exe" (
    echo В сборке нет Downloader.exe — возможно, она сделана старой версией автосборки.
    echo Запустите сборку на GitHub ещё раз.
    goto fail
)

:: ------------------------------------------------ 5. Замена
tasklist /fi "imagename eq Downloader.exe" | find /i "Downloader.exe" >nul
if errorlevel 1 goto replace
echo.
echo Downloader сейчас открыт — закрываю его...
taskkill /im Downloader.exe /f >nul 2>nul
timeout /t 2 >nul

:replace
copy /y "%TMPD%\Downloader.exe" "%~dp0Downloader.exe" >nul
if errorlevel 1 (
    echo Не удалось заменить Downloader.exe — закройте программу и попробуйте снова.
    goto fail
)
>"%INFO%" echo %RUN%
echo   Downloader.exe обновлён

:: ------------------------------------------------ 6. yt-dlp, ffmpeg, deno
:tools
echo.
echo Проверяю yt-dlp, ffmpeg и deno...
if not exist "%~dp0yt-dlp.exe" (
    echo   Скачиваю yt-dlp...
    curl.exe -L --fail --progress-bar -o "%~dp0yt-dlp.exe" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
) else (
    "%~dp0yt-dlp.exe" -U
)
if exist "%~dp0ffmpeg.exe" if exist "%~dp0ffprobe.exe" goto ffmpeg_ok
if not exist "%TMPD%" mkdir "%TMPD%"
echo   Скачиваю ffmpeg...
curl.exe -L --fail --progress-bar -o "%TMPD%\ffmpeg.zip" "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath (Join-Path $env:TMPD 'ffmpeg.zip') -DestinationPath (Join-Path $env:TMPD 'ffmpeg')"
for /r "%TMPD%\ffmpeg" %%F in (ffmpeg.exe ffprobe.exe) do if exist "%%F" copy /y "%%F" "%~dp0" >nul
:ffmpeg_ok
if exist "%~dp0deno.exe" goto deno_ok
if not exist "%TMPD%" mkdir "%TMPD%"
echo   Скачиваю deno...
curl.exe -L --fail --progress-bar -o "%TMPD%\deno.zip" "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath (Join-Path $env:TMPD 'deno.zip') -DestinationPath (Join-Path $env:TMPD 'deno')"
copy /y "%TMPD%\deno\deno.exe" "%~dp0deno.exe" >nul
:deno_ok

:: ------------------------------------------------ готово
echo.
echo ==========================================
echo  Готово!
echo ==========================================
choice /c YN /m "Запустить Downloader"
if errorlevel 2 goto selfupdate
start "" "%~dp0Downloader.exe"

:selfupdate
:: если в сборке пришла новая версия update.bat — подменяем себя последним действием
if exist "%TMPD%\update.bat" (
    fc /b "%TMPD%\update.bat" "%~f0" >nul 2>nul || (copy /y "%TMPD%\update.bat" "%~dp0update.bat.new" >nul)
)
rmdir /s /q "%TMPD%" >nul 2>nul
if exist "%~dp0update.bat.new" (move /y "%~dp0update.bat.new" "%~f0" >nul & exit /b 0)
exit /b 0

:fail
echo.
echo Обновление не выполнено — посмотрите сообщение выше.
pause
exit /b 1
