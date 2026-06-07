"""SiliconFlow preview probe using actual backend prompt format"""

import urllib.request
import json
import time
import os

API_KEY = os.getenv("SILICONFLOW_API_KEY")
BASE_URL = os.getenv("SILICONFLOW_PREVIEW_BASE_URL", "https://api.siliconflow.cn/v1/chat/completions")
MODEL = os.getenv("SILICONFLOW_PREVIEW_MODEL", "inclusionAI/Ling-mini-2.0")

SYSTEM_PROMPT_CHATTY = (
    "You are generating the first spoken preview lead for a voice assistant.\n"
    "Return plain preview lead text only.\n"
    "Do not use JSON, structured field wrappers, markdown, or code fences.\n"
    "Write only one short spoken lead sentence.\n"
    "Keep it natural and immediately speakable.\n"
    "This is a chatty preview. Keep it brief, lively, and natural.\n"
)

SYSTEM_PROMPT_SERIOUS = (
    "You are generating the first spoken preview lead for a voice assistant.\n"
    "Return plain preview lead text only.\n"
    "Do not use JSON, structured field wrappers, markdown, or code fences.\n"
    "Write only one short spoken lead sentence.\n"
    "Keep it natural and immediately speakable.\n"
    "This is a rules or game explanation preview.\n"
    "Keep it brief, confident, and factual in Chinese.\n"
)

def build_user_prompt(mode: str, transcript: str, events: list[dict]) -> str:
    recent_lines = []
    for item in events[-6:]:
        kind = item.get("kind")
        source = item.get("source", "unknown")
        content = item.get("content", "")
        if not content:
            continue
        if kind == "voice_transcript":
            recent_lines.append(f"用户({source}): {content}")
        elif kind == "assistant_spoken":
            recent_lines.append(f"助手: {content}")
        elif kind == "rule_reference":
            recent_lines.append(f"参考({source}): {content}")

    context_block = "\n".join(recent_lines) if recent_lines else "暂无"
    mode_line = "serious" if mode == "serious" else "chatty"
    return (
        f"Current mode: {mode_line}\n"
        f"Latest user request: {transcript}\n"
        f"Recent context:\n{context_block}\n"
        "Output plain preview lead text only.\n"
        "Do not use JSON.\n"
        "Do not explain your thinking.\n"
        "Do not continue into the full answer yet.\n"
        "Only give the first short spoken lead.\n"
    )


def probe(model: str, mode: str, transcript: str, events: list[dict]) -> dict:
    system_prompt = SYSTEM_PROMPT_SERIOUS if mode == "serious" else SYSTEM_PROMPT_CHATTY

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_prompt(mode, transcript, events)},
        ],
        "max_tokens": 50,
        "temperature": 0.45,
        "top_p": 0.8,
        "top_k": 40,
        "frequency_penalty": 0.2,
        "stream": False,
        "enable_thinking": False,
        "n": 1,
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    req = urllib.request.Request(BASE_URL, data=data, headers=headers, method="POST")

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    elapsed = time.time() - t0

    result = json.loads(raw.decode("utf-8"))
    reply = result["choices"][0]["message"]["content"]
    finish = result["choices"][0].get("finish_reason")

    return {
        "model": model,
        "mode": mode,
        "transcript": transcript,
        "elapsed_s": round(elapsed, 3),
        "reply": reply,
        "finish_reason": finish,
        "usage": result.get("usage"),
    }


def main():
    if not API_KEY:
        raise RuntimeError("SILICONFLOW_API_KEY environment variable is not set")

    # Simulate the actual backend prompt scenario
    # Case 1: short transcript, empty history (like the simple probe)
    case1_events = []
    case1_transcript = "你是一个桌游助手，名字叫小美，现在开始介绍你自己，只能说一句话"

    # Case 2: longer transcript, 6-event history (like actual backend)
    case2_events = [
        {"kind": "voice_transcript", "source": "speaker_0", "content": "宝子，我们来玩三国杀吧"},
        {"kind": "assistant_spoken", "source": "companion", "content": "好呀！今天想玩什么模式？"},
        {"kind": "voice_transcript", "source": "speaker_0", "content": "普通场就行，有几个规则我想确认一下"},
        {"kind": "assistant_spoken", "source": "companion", "content": "没问题，说吧！"},
        {"kind": "voice_transcript", "source": "speaker_0", "content": "反贼到底怎么赢？"},
        {"kind": "assistant_spoken", "source": "companion", "content": "反贼的胜利条件是干掉主公..."},
    ]
    case2_transcript = "反贼到底怎么赢？"

    # Case 3: empty events with "serious" mode (simulates the failing case)
    case3_events = []
    case3_transcript = "三国杀反贼怎么赢？"

    for i, (mode, transcript, events, label) in enumerate([
        ("chatty", case1_transcript, case1_events, "simple intro (chatty)"),
        ("serious", case3_transcript, case3_events, "short serious (no history)"),
        ("serious", case2_transcript, case2_events, "serious w/ 6-event history"),
    ], 1):
        print(f"\n--- Case {i}: {label} ---")
        print(f"  transcript: {transcript}")
        print(f"  events count: {len(events)}")
        try:
            r = probe(MODEL, mode, transcript, events)
            print(f"  [{r['elapsed_s']:.3f}s] finish={r['finish_reason']}")
            print(f"  reply: {r['reply']}")
            print(f"  usage: {r['usage']}")
        except Exception as e:
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
