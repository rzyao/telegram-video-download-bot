import os
import requests
import json

# 检查文件
session_file = "telethon_session.session"
file_exists = os.path.exists(session_file)

print(f"Session 文件存在: {file_exists}")
if file_exists:
    print(f"文件路径: {os.path.abspath(session_file)}")
    print(f"文件大小: {os.path.getsize(session_file)} bytes")

# 检查 API
try:
    res = requests.get("http://localhost:8000/api/telegram/status")
    data = res.json()
    print(f"\nAPI 返回: {json.dumps(data, indent=2, ensure_ascii=False)}")
except Exception as e:
    print(f"API 调用失败: {e}")

# 建议
print("\n" + "="*50)
if not file_exists:
    print("✅ Session 已删除，请访问 http://localhost:8000/setup.html")
    print("   应该显示登录表单而非'已登录成功'")
else:
    print("❌ Session 仍然存在！")
    print("   建议手动删除后重启：")
    print("   1. 停止程序 (Ctrl+C)")
    print("   2. rm telethon_session.session*")
    print("   3. python main.py")
