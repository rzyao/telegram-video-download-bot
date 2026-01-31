#!/usr/bin/env python3
"""完整流程测试脚本"""
import requests
import time
import os

BASE_URL = "http://localhost:8000"

def test_flow():
    print("=" * 60)
    print("Telegram Downloader - 完整流程测试")
    print("=" * 60)
    
    # 1. 检查主页
    print("\n[1/5] 检查主页...")
    try:
        res = requests.get(BASE_URL)
        if res.status_code == 200:
            print("✅ 主页可访问")
        else:
            print(f"❌ 主页返回 {res.status_code}")
            return
    except Exception as e:
        print(f"❌ 无法访问主页: {e}")
        return
    
    # 2. 检查 Bot 状态
    print("\n[2/5] 检查 Bot 状态...")
    try:
        res = requests.get(f"{BASE_URL}/api/bot/status")
        data = res.json()
        print(f"   setup_completed: {data.get('setup_completed')}")
        print(f"   session_exists: {data.get('session_exists')}")
        print(f"   client_connected: {data.get('client_connected')}")
        
        if not data.get('client_connected'):
            print("✅ Bot 未连接（符合预期）")
        else:
            print("⚠️  Bot 已连接")
    except Exception as e:
        print(f"❌ 获取状态失败: {e}")
        return
    
    # 3. 检查 Session 文件
    print("\n[3/5] 检查 Session 文件...")
    session_file = "telethon_session.session"
    if os.path.exists(session_file):
        print(f"✅ Session 文件存在: {session_file}")
        print(f"   大小: {os.path.getsize(session_file)} bytes")
    else:
        print(f"ℹ️  Session 文件不存在")
    
    # 4. 测试删除 Session
    print("\n[4/5] 测试删除 Session...")
    if os.path.exists(session_file):
        try:
            res = requests.delete(f"{BASE_URL}/api/telegram/session")
            if res.status_code == 200:
                print("✅ DELETE 请求成功")
                data = res.json()
                print(f"   服务器响应: {data.get('message')}")
                
                # 等待文件删除
                time.sleep(2)
                
                # 验证文件是否删除
                if os.path.exists(session_file):
                    print(f"❌ Session 文件仍然存在！")
                    print(f"   文件路径: {os.path.abspath(session_file)}")
                    print(f"   文件大小: {os.path.getsize(session_file)} bytes")
                else:
                    print("✅ Session 文件已成功删除！")
            else:
                print(f"❌ DELETE 请求失败: {res.status_code}")
                print(f"   响应: {res.text}")
        except Exception as e:
            print(f"❌ 删除请求失败: {e}")
    else:
        print("ℹ️  Session 文件不存在，跳过删除测试")
    
    # 5. 检查 /api/telegram/status
    print("\n[5/5] 验证登录状态...")
    try:
        res = requests.get(f"{BASE_URL}/api/telegram/status")
        data = res.json()
        if data.get('logged_in'):
            print(f"❌ API 仍显示已登录！")
        else:
            print(f"✅ API 正确显示未登录")
    except Exception as e:
        print(f"❌ 检查失败: {e}")
    
    # 总结
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    
    print("\n下一步：")
    print("1. 访问 http://localhost:8000")
    print("2. 点击'重新登录'按钮")
    print("3. 观察是否跳转到 /setup.html")
    print("4. 检查是否显示登录表单（而非'已登录成功'）")

if __name__ == "__main__":
    test_flow()
