@echo off
REM Build a single double-clickable "Airfoil Converter.exe" into dist\.
REM Requires: pip install pyinstaller
pyinstaller --onefile --windowed --name "Airfoil Converter v1.4" ^
    --paths src ^
    main.py
