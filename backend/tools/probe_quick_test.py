import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gamevoice_server.dialog_client import MiniMaxDialogClient

key = os.environ.get("MINIMAX_API_KEY", "")
print(f"Key length: {len(key)}")

client = MiniMaxDialogClient(
    api_key=key,
    model="MiniMax-M2.7-highspeed",
    base_url="https://api.minimaxi.com/v1/text/chatcompletion_v2",
    timeout_seconds=20.0,
)

payload = {
    "model": client.model,
    "stream": False,
    "max_completion_tokens": 30,
    "temperature": 0.4,
    "top_p": 0.95,
    "messages": [{"role": "user", "content": "hi"}],
}
body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
headers = {
    "Authorization": "Bearer " + key,
    "Content-Type": "application/json",
}

print("Sending request...")
try:
    resp = client._request_sender(client.base_url, body, headers, 20.0)
    parsed = client._parse_text_post_response(resp)
    print("Response:", str(parsed)[:500])
except Exception as e:
    print("Error:", e)
