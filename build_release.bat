@echo off
setlocal
cd /d "%~dp0"

set "APP_VER=1.1.0"
if not exist "release" mkdir "release"

if exist "dist" (
  echo [0/5] Clearing read-only attributes and old dist folder...
  attrib -R /S /D "dist\*" >nul 2>&1
  rmdir /s /q "dist"
)

echo [1/5] Generating songpulse.ico...
python song_id_app.pyw --make-icon
if errorlevel 1 (
  echo Icon generation failed!
  pause
  exit /b 1
)

echo [2/5] Building slim SongPulse Core (no heavy AI packages)...
pyinstaller --noconfirm SongPulse_Core.spec
if errorlevel 1 (
  echo Core build failed!
  pause
  exit /b 1
)

echo [3/5] Building full SongPulse (for optional AI Engine Pack diff)...
pyinstaller --noconfirm SongPulse.spec
if errorlevel 1 (
  echo Full build failed!
  pause
  exit /b 1
)

echo [4/5] Packaging optional release\SongPulse_AI_Pack_v%APP_VER%.zip...
python -c "import os, shutil, zipfile; core_root=r'dist\SongPulse_Core\_internal'; full_root=r'dist\SongPulse\_internal'; out_zip=r'release\SongPulse_AI_Pack_v%APP_VER%.zip'; core_files=set(); [core_files.add(os.path.relpath(os.path.join(r,f), core_root).lower()) for r,_,fs in os.walk(core_root) for f in fs]; zf=zipfile.ZipFile(out_zip,'w',zipfile.ZIP_DEFLATED,compresslevel=6); count=0; exec('for r,_,fs in os.walk(full_root):\n for f in fs:\n  full_p=os.path.join(r,f)\n  rel=os.path.relpath(full_p, full_root)\n  if rel.lower() not in core_files:\n   zf.write(full_p, rel)\n   count+=1'); ff=shutil.which('ffmpeg'); (zf.write(ff, r'bin\ffmpeg.exe') if ff and os.path.isfile(ff) else None); zf.close(); print(f'Packed {count} optional AI files into {out_zip} (ffmpeg included: {bool(ff)})')"
if errorlevel 1 (
  echo AI Pack zip failed!
  pause
  exit /b 1
)

echo [5/5] Compiling Inno Setup installer...
set "ISCC=C:\Program Files\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if exist "%ISCC%" (
  "%ISCC%" SongPulse_Setup.iss
  echo.
  echo Done!
  echo  - Core Installer : release\SongPulse_Setup_v%APP_VER%.exe
  echo  - Optional AI Pack: release\SongPulse_AI_Pack_v%APP_VER%.zip
  echo Upload BOTH files to GitHub Release v%APP_VER%!
) else (
  echo Inno Setup not found automatically. Open SongPulse_Setup.iss in Inno Setup and click Build -^> Compile.
)
pause
