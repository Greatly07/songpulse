@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Generating songpulse.ico...
python song_id_app.pyw --make-icon

echo [2/3] Building SongPulse.exe with PyInstaller...
pyinstaller --noconfirm --onedir --windowed ^
  --name "SongPulse" ^
  --icon "songpulse.ico" ^
  --collect-submodules songpulse ^
  --collect-data audio_separator ^
  --collect-all shazamio ^
  --collect-all shazamio_core ^
  --collect-binaries onnxruntime ^
  --hidden-import PyQt6.QtMultimedia ^
  --hidden-import _cffi_backend ^
  --hidden-import soundcard ^
  song_id_app.pyw

echo [3/3] Compiling Inno Setup installer...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if exist "%ISCC%" (
  "%ISCC%" SongPulse_Setup.iss
  echo.
  echo Done! Installer saved to: release\SongPulse_Setup_v1.0.0.exe
) else (
  echo Inno Setup 6 not found automatically. Open SongPulse_Setup.iss in Inno Setup and click Build -^> Compile.
)
pause