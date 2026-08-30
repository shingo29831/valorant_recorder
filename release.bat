@echo off
setlocal enabledelayedexpansion

:: 引数の取得 (major, minor, patch)。指定がなければ patch
set BUMP_TYPE=%1
if "%BUMP_TYPE%"=="" set BUMP_TYPE=patch

echo [1/6] Bumping version (%BUMP_TYPE%)...
for /f "tokens=*" %%i in ('python tools\bump_version.py %BUMP_TYPE%') do set NEW_VERSION=%%i
echo New version is: %NEW_VERSION%

echo.
echo [2/5] Building main.exe and Installer...
call build_installer.bat

echo.
echo [3/5] Creating update.zip...
if exist update.zip del update.zip
REM Use PowerShell to compress ValoReco.exe into update.zip
powershell -Command "Compress-Archive -Path ValoReco.exe -DestinationPath update.zip -Force"

echo.
echo [4/5] Preparing release directory...
set RELEASE_DIR=..\valorant-recorder-release
if not exist "%RELEASE_DIR%" (
    echo [Error] Release directory "%RELEASE_DIR%" does not exist.
    echo Please clone your GitHub repository to that location first.
    exit /b 1
)

REM Generate version.json for Cloudflare Pages
REM Worker returns a 302 redirect to GitHub's S3, so it bypasses the 25MB limit.
echo { "version": "%NEW_VERSION%", "download_url": "https://valoreco-api.meld-task.com/download/update" } > "%RELEASE_DIR%\version.json"

echo.
echo [5/5] Copying files to release repository...
copy /Y _worker.js "%RELEASE_DIR%\_worker.js"

REM Clean up any binaries previously copied to the git repository to avoid 25MB Pages limit
if exist "%RELEASE_DIR%\update.zip" del "%RELEASE_DIR%\update.zip"
if exist "%RELEASE_DIR%\ValoReco_Setup.exe" del "%RELEASE_DIR%\ValoReco_Setup.exe"

echo.
echo ===================================================
echo Release preparation complete! Version: %NEW_VERSION%
echo ===================================================

set /p DO_PUSH="Do you want to automatically commit and push to GitHub? (Y/N): "
if /I "%DO_PUSH%"=="Y" (
    echo.
    echo [6/6] Committing and pushing to release repository...
    pushd "%RELEASE_DIR%"
    git add -A
    git commit -m "Release version %NEW_VERSION%"
    git push
    popd
    echo.
    echo Push complete! Cloudflare Pages deployment should be triggered automatically.
    
    echo.
    echo Checking for GitHub CLI ^(gh^) to upload binaries to GitHub Releases...
    where gh >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        echo Creating GitHub Release v%NEW_VERSION%...
        gh release create v%NEW_VERSION% update.zip Output\ValoReco_Setup.exe --repo shingo29831/valorant-recorder-release --title "v%NEW_VERSION%" --notes "Release v%NEW_VERSION%"
        echo GitHub Release created and binaries uploaded successfully!
    ) else (
        echo [Warning] GitHub CLI ^(gh^) not found.
        echo Please manually create a release named "v%NEW_VERSION%" on GitHub:
        echo https://github.com/shingo29831/valorant-recorder-release/releases/new
        echo And upload 'update.zip' and 'Output\ValoReco_Setup.exe' to it.
    )
) else (
    echo.
    echo Next steps:
    echo 1. cd "%RELEASE_DIR%"
    echo 2. git add -A
    echo 3. git commit -m "Release version %NEW_VERSION%"
    echo 4. git push
    echo 5. Create a GitHub Release named "v%NEW_VERSION%" and upload update.zip ^& ValoReco_Setup.exe
)

pause
