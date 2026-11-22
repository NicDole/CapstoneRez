@echo off
REM Build the standalone Coil Tracker app (Windows). Run from the repository root:
REM     packaging\build_exe.bat
REM Needs: Python 3.11+ with  pip install -r packaging\requirements-app.txt
REM Output: dist\CoilTracker\CoilTracker.exe  (copy the whole dist\CoilTracker folder)
REM Check a build without clicking (writes the result to selftest.txt):
REM     dist\CoilTracker\CoilTracker.exe --selftest selftest.txt examples\demo_library [SNRMAP.dat [MPRAGE.dat]]
REM Needs PyInstaller 6.22+ (older versions break numpy 2.4 / scipy 1.17 in the bundle). twixtools needs scipy.

python -m PyInstaller --noconfirm --clean --windowed --name CoilTracker ^
  --collect-submodules twixtools ^
  --exclude-module tkinter --exclude-module nibabel --exclude-module skimage ^
  --exclude-module pandas --exclude-module IPython --exclude-module PyQt5 --exclude-module PyQt6 ^
  --exclude-module pytest ^
  run_coil_tracker.py
