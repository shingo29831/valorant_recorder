@echo off
setlocal enabledelayedexpansion

set "BUMP_TYPE=%~1"
if not "%BUMP_TYPE%"=="" goto :start_release

call :ask_yn "Patch release (x.y.z+1)?" ANS
if "%ANS%"=="CANCEL" goto :cancelled
if "%ANS%"=="Y" (
    set "BUMP_TYPE=patch"
    goto :start_release
)

call :ask_yn "Minor release (x.y+1.0)?" ANS
if "%ANS%"=="CANCEL" goto :cancelled
if "%ANS%"=="Y" (
    set "BUMP_TYPE=minor"
    goto :start_release
)

call :ask_yn "Major release (x+1.0.0)?" ANS
if "%ANS%"=="CANCEL" goto :cancelled
if "%ANS%"=="Y" (
    set "BUMP_TYPE=major"
    goto :start_release
)

echo.
echo No release type selected. Exiting.
exit /b 0

:cancelled
echo.
echo Release cancelled by user.
exit /b 0

:start_release
echo.
echo [1/6] Bumping version (%BUMP_TYPE%)...
set "VER_TMP=%TEMP%\valoreco_ver_%RANDOM%.tmp"
python tools\bump_version.py %BUMP_TYPE% > "%VER_TMP%"
if errorlevel 1 (
    echo [Error] Failed to bump version.
    del "%VER_TMP%" 2>nul
    exit /b 1
)
set /p NEW_VERSION=<"%VER_TMP%"
del "%VER_TMP%" 2>nul
if "%NEW_VERSION%"=="" (
    echo [Error] Failed to read bumped version.
    exit /b 1
)
echo New version is: %NEW_VERSION%

echo.
echo [2/6] Building main.exe and Installer...
call build_installer.bat --no-pause
if errorlevel 1 (
    echo [Error] build_installer.bat failed.
    exit /b 1
)

echo.
echo [3/6] Creating update.zip...
if exist update.zip del update.zip
powershell -NoProfile -Command "Compress-Archive -Path ValoReco.exe -DestinationPath update.zip -Force"
if errorlevel 1 (
    echo [Error] Failed to create update.zip.
    exit /b 1
)

echo.
echo [4/6] Preparing release directory...
set "RELEASE_DIR=..\valorant-recorder-release"
if not exist "%RELEASE_DIR%" (
    echo [Error] Release directory "%RELEASE_DIR%" does not exist.
    echo Please clone your GitHub repository to that location first.
    exit /b 1
)

REM Worker returns a 302 redirect to GitHub's S3, so it bypasses the 25MB limit.
echo { "version": "%NEW_VERSION%", "download_url": "https://valoreco-api.meld-task.com/download/update" } > "%RELEASE_DIR%\version.json"

echo.
echo [5/6] Copying files to release repository...
copy /Y _worker.js "%RELEASE_DIR%\_worker.js"
if errorlevel 1 (
    echo [Error] Failed to copy _worker.js.
    exit /b 1
)

if exist "%RELEASE_DIR%\update.zip" del "%RELEASE_DIR%\update.zip"
if exist "%RELEASE_DIR%\ValoReco_Setup.exe" del "%RELEASE_DIR%\ValoReco_Setup.exe"

echo.
echo [6/6] Committing and pushing to release repository...
pushd "%RELEASE_DIR%"
git add -A
git commit -m "Release version %NEW_VERSION%"
if errorlevel 1 (
    popd
    echo [Error] git commit failed.
    exit /b 1
)
git push
if errorlevel 1 (
    popd
    echo [Error] git push failed.
    exit /b 1
)
popd
echo Push complete! Cloudflare Pages deployment should be triggered automatically.

echo.
echo Checking for GitHub CLI ^(gh^) to upload binaries to GitHub Releases...
where gh >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Creating GitHub Release v%NEW_VERSION%...
    gh release create v%NEW_VERSION% update.zip Output\ValoReco_Setup.exe --repo shingo29831/valorant-recorder-release --title "v%NEW_VERSION%" --notes "Release v%NEW_VERSION%"
    if errorlevel 1 (
        echo [Error] Failed to create GitHub Release.
        exit /b 1
    )
    echo GitHub Release created and binaries uploaded successfully!
) else (
    echo [Warning] GitHub CLI ^(gh^) not found.
    echo Please manually create a release named "v%NEW_VERSION%" on GitHub:
    echo https://github.com/shingo29831/valorant-recorder-release/releases/new
    echo And upload 'update.zip' and 'Output\ValoReco_Setup.exe' to it.
)

echo.
echo ===================================================
echo Release complete! Version: %NEW_VERSION%
echo ===================================================
exit /b 0

:ask_yn
set "%~2="
<nul set /p "=%~1 [Enter/Space: y, Esc: cancel, Other: n]: "
powershell -NoProfile -Command "while([Console]::KeyAvailable){[void][Console]::ReadKey($true)}; $k=[Console]::ReadKey($true); if($k.Key -eq 'Enter' -or $k.Key -eq 'Spacebar' -or $k.Key -eq 'Y'){exit 0} elseif($k.Key -eq 'Escape'){exit 2} else{exit 1}"
set "KEY_RES=%ERRORLEVEL%"
if "%KEY_RES%"=="0" (
    echo y
    set "%~2=Y"
) else if "%KEY_RES%"=="2" (
    echo cancel
    set "%~2=CANCEL"
) else (
    echo n
    set "%~2=N"
)
exit /b 0
