@echo off
rem Builds dist\MTGCollectionManager.exe (needs the .venv of this project and PyInstaller).
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install --quiet pyinstaller || goto :fail
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name MTGCollectionManager ^
  --collect-submodules uvicorn --collect-submodules websockets --collect-all apscheduler ^
  --add-data "app\templates;app\templates" ^
  --add-data "app\static;app\static" ^
  --add-data "app\stream\static;app\stream\static" ^
  --add-data "app\cardmarket_selectors.json;app" ^
  launcher.py || goto :fail
echo.
echo Done: dist\MTGCollectionManager.exe
echo Put it in a folder of its own (or next to your mtg.db) and start it.
exit /b 0
:fail
echo The build failed.
exit /b 1
