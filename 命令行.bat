@echo off
cd /d "%~dp0"
echo.
echo   1  New game (seed 42)
echo   2  What can I do this week
echo   3  Advance 8 weeks
echo   4  Event tree
echo   5  Monthly report
echo   6  Run tests
echo   7  Balance sweep
echo   0  Exit
echo.
set /p pick=Number: 
if "%pick%"=="1" py -3 sim_cli.py new --seed 42 --slot dev & py -3 sim_cli.py preview --slot dev
if "%pick%"=="2" py -3 sim_cli.py act --list --slot dev
if "%pick%"=="3" py -3 sim_cli.py sim --weeks 8 --slot dev
if "%pick%"=="4" py -3 sim_cli.py events --slot dev
if "%pick%"=="5" py -3 sim_cli.py month --slot dev
if "%pick%"=="6" py -3 -m pytest -q
if "%pick%"=="7" py -3 tools/balance.py --seeds 10 --weeks 24
echo.
pause
