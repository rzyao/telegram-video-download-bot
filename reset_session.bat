@echo off
echo ========================================
echo Telegram Downloader - 重置 Session
echo ========================================
echo.

echo [1/3] 停止所有 Python 进程...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/3] 删除 Session 文件...
del /F /Q telethon_session.session* 2>nul

echo [3/3] 验证删除...
if exist telethon_session.session (
    echo ❌ 删除失败！请手动删除后重试
    pause
    exit /b 1
) else (
    echo ✅ Session 已删除
)

echo.
echo ========================================
echo 请运行: python main.py
echo 然后访问: http://localhost:8000/setup.html
echo ========================================
pause
