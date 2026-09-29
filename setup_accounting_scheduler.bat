@echo off
rem 毎月3日 9:00 に run_monthly_close.bat を実行するタスクを登録する
%SystemRoot%\System32\schtasks.exe /create /tn "Accounting_Monthly_Close" /tr "%~dp0run_monthly_close.bat" /sc monthly /d 3 /st 09:00 /ru "%USERNAME%" /f
echo Done.
pause
