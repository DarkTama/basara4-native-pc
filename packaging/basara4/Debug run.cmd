@echo off
rem Basara 4 PC: opt-in debug run (cutscene ghost / double picture). Normal play is "Play Sengoku Basara 4.cmd".
rem Starts tools\debug_watch.py (needs Python 3 on PATH) next to the game; it writes user\timeline.log, user\captures,
rem user\framedump, user\recordings and user\report.txt. Press F9 when the symptom shows. Set DEBUG_NO_VIDEO=1 to skip
rem the screen recording. Optional: set SPARTAN_HALF_TARGET_FIX=0 before starting to test without the ghost fix.
cd /d "%~dp0"
set "U=%~dp0user"
if not exist "%U%" mkdir "%U%"
set "SPARTAN_ROOT=%~dp0"
set "SPARTAN_CAPTURE_TRIGGER=%U%\capture.trigger"
set "SPARTAN_FRAME_DUMP_TRIGGER=%U%\framedump.trigger"
set "SPARTAN_DUMP_DIR=%U%\framedump"
set "SPARTAN_GPU_USER_DIR=%U%"
if not defined SPARTAN_HALF_TARGET_FIX set "SPARTAN_HALF_TARGET_FIX=1"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required for the recorder ^(tools\debug_watch.py^); starting the game without it.
) else (
  start "" /b python "%~dp0tools\debug_watch.py" "%U%"
)
start "" "%~dp0Sengoku Basara 4.exe" --play
