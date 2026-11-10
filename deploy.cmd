@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set IMAGE_NAME=zzabek/minenode:latest

echo ==> Sprawdzanie stanu Docker Engine...

:: 1. Sprawdzenie czy daemon Dockera odpowiada
docker info >nul 2>&1
if %errorlevel% equ 0 (
    echo ==> Docker Engine już działa.
    goto :BUILD
)

:: 2. Uruchomienie Docker Desktop w tle, jeśli engine jest wyłączony
echo ==> Docker Engine jest wyłączony. Uruchamianie Docker Desktop...

set "DOCKER_PATH=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%DOCKER_PATH%" (
    start "" "%DOCKER_PATH%"
) else (
    start "" "docker-desktop://"
)

echo ==> Oczekiwanie na gotowość Docker Engine...
set RETRIES=0
set MAX_RETRIES=30

:WAIT_LOOP
timeout /t 3 /nobreak >nul
set /a RETRIES+=1
docker info >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo ==> Docker Engine jest gotowy!
    goto :BUILD
)
<nul set /p =.
if %RETRIES% lss %MAX_RETRIES% goto :WAIT_LOOP

echo.
echo [BŁĄD] Przekroczono limit czasu oczekiwania na uruchomienie Dockera.
goto :END

:BUILD
:: 3. Budowanie obrazu
echo ==> Budowanie obrazu: %IMAGE_NAME%...
docker build -t %IMAGE_NAME% .

if %errorlevel% neq 0 (
    echo [BŁĄD] Wystąpił błąd podczas budowania obrazu.
    goto :END
)

:: 4. Wysyłanie obrazu na Docker Hub
echo ==> Wysyłanie obrazu na Docker Hub...
docker push %IMAGE_NAME%

if %errorlevel% equ 0 (
    echo ==> Sukces! Obraz %IMAGE_NAME% został pomyślnie wysłany.
) else (
    echo [BŁĄD] Błąd podczas wysyłania obrazu na Docker Hub. Upewnij się, że jesteś zalogowany (docker login).
)

:END
echo.
echo Nacisnij dowolny klawisz, aby zamknąć to okno...
pause >nul