import sys
sys.path.insert(0, r'c:\project\Bot')

import main

print(f"client: {main.client}")
print(f"client_connected: {main.client_connected}")
print(f"downloader: {main.downloader}")

if main.client:
    print(f"client.is_connected(): {main.client.is_connected()}")
