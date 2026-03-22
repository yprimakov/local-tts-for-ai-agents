@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Kokoro TTS -- Health Check
echo ============================================================

set PASS=0
set FAIL=0

:: -- 1. venv exists -------------------------------------------
echo.
echo [1] Python venv...
if exist "venv\Scripts\python.exe" (
    echo     OK  venv\Scripts\python.exe found
    set /a PASS+=1
) else (
    echo     FAIL  venv\Scripts\python.exe NOT FOUND
    echo           Run:  python setup.py
    set /a FAIL+=1
)

:: -- 2. Models ------------------------------------------------
echo.
echo [2] Model files...
if exist "models\kokoro-v1.0.onnx" (
    echo     OK  kokoro-v1.0.onnx found
    set /a PASS+=1
) else (
    echo     FAIL  models\kokoro-v1.0.onnx NOT FOUND
    echo           Run:  python setup.py
    set /a FAIL+=1
)
if exist "models\voices-v1.0.bin" (
    echo     OK  voices-v1.0.bin found
    set /a PASS+=1
) else (
    echo     FAIL  models\voices-v1.0.bin NOT FOUND
    echo           Run:  python setup.py
    set /a FAIL+=1
)

:: -- 3. Python packages ---------------------------------------
echo.
echo [3] Python packages...
"venv\Scripts\python.exe" -c "import kokoro_onnx, sounddevice, soundfile, onnxruntime; print('    OK  kokoro_onnx, sounddevice, soundfile, onnxruntime all import OK')" 2>"%TEMP%\kokoro_pkg_err.txt"
if %ERRORLEVEL%==0 (
    set /a PASS+=1
) else (
    echo     FAIL  Package import error:
    type "%TEMP%\kokoro_pkg_err.txt"
    echo           Run:  python setup.py
    set /a FAIL+=1
)

:: -- 4. Claude Code hook registered ---------------------------
echo.
echo [4] Claude Code Stop hook...
"venv\Scripts\python.exe" -c "import json,os; s=json.load(open(os.path.expanduser('~/.claude/settings.json'))); hooks=[h.get('command','') for section in s.get('hooks',{}).get('Stop',[]) for h in section.get('hooks',[])]; found=[h for h in hooks if 'local-tts-for-ai-agents' in h.replace('\\\\','/')]; print('    OK  Hook registered: '+found[0]) if found else print('    FAIL  Hook NOT found in settings.json')" 2>&1
if %ERRORLEVEL%==0 (
    set /a PASS+=1
) else (
    echo           Run:  python setup.py
    set /a FAIL+=1
)

:: -- 5. Voice toggle state ------------------------------------
echo.
echo [5] Voice responses toggle...
if exist "%USERPROFILE%\.claude\voice_enabled" (
    echo     ON   Voice responses are ENABLED
) else (
    echo     OFF  Voice responses are DISABLED
    echo          To enable:  type nul ^> "%%USERPROFILE%%\.claude\voice_enabled"
    echo          Or press Ctrl+Alt+V while AHK is running
)

:: -- 6. AutoHotkey running ------------------------------------
echo.
echo [6] AutoHotkey process...
tasklist /FI "IMAGENAME eq AutoHotkey64.exe" 2>nul | find /I "AutoHotkey" >nul
if %ERRORLEVEL%==0 (
    echo     OK  AutoHotkey is running
    set /a PASS+=1
) else (
    tasklist /FI "IMAGENAME eq AutoHotkey.exe" 2>nul | find /I "AutoHotkey" >nul
    if %ERRORLEVEL%==0 (
        echo     OK  AutoHotkey is running
        set /a PASS+=1
    ) else (
        echo     WARN  AutoHotkey is NOT running
        echo          Double-click kokoro_hotkey.ahk to start it
    )
)

:: -- 7. Hook log (last 5 lines) --------------------------------
echo.
echo [7] Hook log (last activity)...
if exist "%TEMP%\kokoro_hook.log" (
    echo     --- %TEMP%\kokoro_hook.log ---
    powershell -Command "Get-Content '%TEMP%\kokoro_hook.log' -Tail 6 | ForEach-Object { '     ' + $_ }"
) else (
    echo     No log file yet -- hook has not fired since last restart
    echo     Make sure Claude Code was restarted after setup
)

:: -- 8. Quick audio test --------------------------------------
echo.
echo [8] Audio generation test (3-second test)...
echo     Generating a short test phrase -- you should hear it play...
"venv\Scripts\python.exe" tts.py "Kokoro TTS is working correctly." --autoplay 2>"%TEMP%\kokoro_test_err.txt"
if %ERRORLEVEL%==0 (
    echo     OK  Audio played successfully
    set /a PASS+=1
) else (
    echo     FAIL  Audio generation failed:
    type "%TEMP%\kokoro_test_err.txt"
    set /a FAIL+=1
)

:: -- Summary --------------------------------------------------
echo.
echo ============================================================
echo   Results: %PASS% passed  /  %FAIL% failed
echo ============================================================
echo.
if %FAIL%==0 (
    echo   All checks passed. If Ctrl+Alt+R still does not work:
    echo     1. Right-click the H icon in the taskbar
    echo     2. Click "Reload Script"
    echo     3. Select text, then press Ctrl+Alt+R
    echo        -- A tray tip should appear saying "Generating audio..."
) else (
    echo   Fix the failed checks above, then run doctor.bat again.
)
echo.
pause
