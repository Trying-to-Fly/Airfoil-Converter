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
REM The bundled faces (Libre Franklin, JetBrains Mono) are registered privately
REM at launch. The folder is carried whether or not the .ttf files are in it: a
REM build without them falls back to Segoe UI and Consolas, which is a design
REM the window is meant to survive, not a failure.
pyinstaller --onefile --windowed --name "Airfoil Converter v1.8" ^
    --paths src ^
    --add-data "src\airfoil_converter\assets\fonts;assets/fonts" ^
    --hidden-import pythoncom ^
    --hidden-import pywintypes ^
    --hidden-import win32com.client.dynamic ^
    --hidden-import win32timezone ^
    main.py
