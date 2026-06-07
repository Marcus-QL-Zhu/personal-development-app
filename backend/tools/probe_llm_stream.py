#!/usr/bin/env python3
"""
Debug probe: test MiniMax streaming response parsing.
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gamevoice_server.dialog_client import MiniMaxDialogClient
from gamevoice_server.config import settings

def main():
    key = os.environ.get("MINIMAX_API_KEY", "")
    print(f"Key length: {len(key)}")

    client = MiniMaxDialogClient(
        api_key=key,
        model="MiniMax-M2.7-highspeed",
        base_url="https://api.minimaxi.com/v1/text/chatcompletion_v2",
        timeout_seconds=20.0,
    )

    messages = [
        {"role": "system", "name": "MiniMax AI", "content": "你是一个智能助手。你可以使用工具来回答问题。"},
        {"role": "user", "name": "用户", "content": "用arkham_cards工具查询60301这张卡，告诉我结果。"},
    ]

    payload = {
        "model": client.model,
        "stream": True,
        "max_completion_tokens": 500,
        "temperature": 0.4,
        "top_p": 0.95,
        "messages": messages,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    print("Sending streaming request...")
    resp_bytes = client._request_sender(client.base_url, body, headers, 30.0)
    print(f"Raw bytes (first 500): {resp_bytes[:500]}")

    print("\nParsing as streaming response...")
    parsed = client._parse_text_post_response(resp_bytes)
    print(f"Parsed keys: {list(parsed.keys())}")
    print(f"Choices: {parsed.get('choices')}")

    text = client._extract_text(parsed)
    print(f"\nextracted text: {repr(text[:300])}")

if __name__ == "__main__":
    main()
