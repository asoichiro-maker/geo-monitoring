@echo off
rem 請求書フォルダを取り込み、前月の売上実績を確定する（毎月3日にタスクスケジューラから実行）
cd /d %~dp0
if not exist logs mkdir logs
python -m accounting.cli monthly >> logs\monthly_close.log 2>&1
