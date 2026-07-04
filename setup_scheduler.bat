@echo off
if not exist "C:\Users\akimo\Desktop\geo_poc\logs" mkdir "C:\Users\akimo\Desktop\geo_poc\logs"
%SystemRoot%\System32\schtasks.exe /create /tn "GEO_Monitoring_Pipeline" /tr "C:\Users\akimo\Desktop\geo_poc\run_pipeline.bat" /sc daily /st 00:00 /ru "%USERNAME%" /f
echo Done.
pause
