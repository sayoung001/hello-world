@echo off
rem 세이브티커 푸시 — 10분마다 Downloads\saveticker\*.csv 를 git 저장소에 올린다.
rem 창을 닫으면 멈춘다. 창 없이 돌리려면 작업 스케줄러에 등록(pc\README.md).
cd /d "%~dp0"
python "%~dp0saveticker_push.py" %*
pause
