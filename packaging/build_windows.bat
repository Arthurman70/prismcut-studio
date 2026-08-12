@echo off
REM ------------------------------------------------------------------
REM  Build PrismCut.exe on Windows (one command, from the repo root):
REM      packaging\build_windows.bat
REM  Requires Python 3.10+ from python.org on PATH.
REM ------------------------------------------------------------------
cd /d "%~dp0.."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller pillow
python packaging\make_icon.py
python -m PyInstaller packaging\prismcut.spec --noconfirm
echo.
echo ==================================================================
echo  Done! Your app is in  dist\PrismCut\PrismCut.exe
echo  (Ship the whole dist\PrismCut folder, or zip it.)
echo.
echo  For a proper Setup.exe installer, install Inno Setup 6
echo  (https://jrsoftware.org/isinfo.php) then run:
echo    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
echo ==================================================================
pause
