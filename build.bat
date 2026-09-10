@echo off
REM Build a single double-clickable "Airfoil Converter.exe" into dist\.
REM Requires: pip install pyinstaller pywin32
REM
REM The hidden imports are pywin32's. PyInstaller cannot see them through the
REM lazy import in swcom.py, and without them the SolidWorks link is missing
REM from the build with no error at all -- the app just never finds SolidWorks.
REM
REM If a build misbehaves, drop --windowed first: it swallows import errors, so
REM a failure shows up as a window that never opens.
pyinstaller --onefile --windowed --name "Airfoil Converter v1.5" ^
    --paths src ^
    --hidden-import pythoncom ^
    --hidden-import pywintypes ^
    --hidden-import win32com.client.dynamic ^
    --hidden-import win32timezone ^
    main.py
