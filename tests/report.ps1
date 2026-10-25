# Показывает отчёт самопроверки в логе и в аннотациях сборки (видны на странице запуска).
param([string]$LogPath, [string]$Title, [int]$ExitCode)

if (Test-Path $LogPath) {
    $text = Get-Content $LogPath -Raw -Encoding utf8
} else {
    $text = "Отчёт не создан: программа завершилась раньше, чем начала проверку (код $ExitCode)."
}
Write-Host $text

$msg = $text -replace '%', '%25' -replace "`r", '' -replace "`n", '%0A'
$level = if ($ExitCode -eq 0) { 'notice' } else { 'error' }
Write-Host "::$level title=$Title::$msg"

if ($ExitCode -ne 0) { exit 1 }
