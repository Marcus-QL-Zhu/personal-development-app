from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .config import Settings, settings
from .dialog_client import (
    MiniMaxDialogClient,
    normalize_reply_payload,
    looks_truncated_conversation_reply,
)

TEXT_POST_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"


@dataclass(frozen=True)
class ProbeVariant:
    name: str
    endpoint_mode: Literal["anthropic", "text_post"]
    model: str
    max_output_tokens: int
    temperature: float
    top_p: float = 0.95
    stream: bool = False


@dataclass(frozen=True)
class ProbeScenario:
    name: str
    mode: str
    transcript: str
    events: list[dict]


def default_variants() -> list[ProbeVariant]:
    return [
        ProbeVariant(
            name="anthropic_current",
            endpoint_mode="anthropic",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=420,
            temperature=0.55,
            top_p=0.95,
            stream=False,
        ),
        ProbeVariant(
            name="anthropic_900_balanced",
            endpoint_mode="anthropic",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=900,
            temperature=0.40,
            top_p=0.95,
            stream=False,
        ),
        ProbeVariant(
            name="anthropic_1200_balanced",
            endpoint_mode="anthropic",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=1200,
            temperature=0.35,
            top_p=0.95,
            stream=False,
        ),
        ProbeVariant(
            name="text_post_900_stream",
            endpoint_mode="text_post",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=900,
            temperature=0.40,
            top_p=0.95,
            stream=True,
        ),
        ProbeVariant(
            name="text_post_1200_stream",
            endpoint_mode="text_post",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=1200,
            temperature=0.35,
            top_p=0.95,
            stream=True,
        ),
    ]


def default_scenarios() -> list[ProbeScenario]:
    return [
        ProbeScenario(
            name="conversation_joke",
            mode="conversation",
            transcript="宝子，给我讲个笑话。",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "宝子，给我讲个笑话。"}],
        ),
        ProbeScenario(
            name="conversation_game_intro",
            mode="conversation",
            transcript="宝子，给我介绍一下 Arkham Horror 这个游戏。",
            events=[
                {
                    "kind": "voice_transcript",
                    "source": "live_asr",
                    "content": "宝子，给我介绍一下 Arkham Horror 这个游戏。",
                }
            ],
        ),
        ProbeScenario(
            name="conversation_rule_explain",
            mode="conversation",
            transcript="解释一下这条规则。",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "解释一下这条规则。"}],
        ),
        ProbeScenario(
            name="conversation_game_rules_intro",
            mode="conversation",
            transcript="给我介绍一下 Arkham Horror 的基础规则。",
            events=[
                {
                    "kind": "voice_transcript",
                    "source": "live_asr",
                    "content": "给我介绍一下 Arkham Horror 的基础规则。",
                }
            ],
        ),
    ]


def _extract_text_from_anthropic(response: dict) -> str:
    return "".join(
        block.get("text", "")
        for block in response.get("content", [])
        if block.get("type") == "text" and block.get("text")
    ).strip()


def _extract_text_from_text_post(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _extract_stop_reason(endpoint_mode: str, response: dict | None) -> str | None:
    if not response:
        return None
    if endpoint_mode == "text_post":
        choices = response.get("choices") or []
        return choices[0].get("finish_reason") if choices else None
    return response.get("stop_reason")


def _parse_streaming_response(endpoint_mode: str, response_bytes: bytes) -> dict:
    decoded = response_bytes.decode("utf-8", errors="ignore")
    if "data:" not in decoded:
        return json.loads(decoded)

    chunks: list[dict] = []
    for line in decoded.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunks.append(json.loads(payload))
        except json.JSONDecodeError:
            continue

    if endpoint_mode == "text_post":
        latest_text = ""
        finish_reason: str | None = None
        for chunk in chunks:
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            text = delta.get("content") or message.get("content") or ""
            if text:
                latest_text = text
        return {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": latest_text,
                    },
                }
            ],
            "raw_chunks": chunks,
        }

    text_parts = []
    stop_reason: str | None = None
    for chunk in chunks:
        chunk_type = chunk.get("type")
        if chunk_type == "content_block_delta":
            delta = chunk.get("delta") or {}
            text = delta.get("text") or ""
            if text:
                text_parts.append(text)
        elif chunk_type == "message_delta":
            delta = chunk.get("delta") or {}
            stop_reason = delta.get("stop_reason") or stop_reason
        stop_reason = chunk.get("stop_reason") or stop_reason
    return {
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": "".join(text_parts)}],
        "raw_chunks": chunks,
    }


def build_probe_payload(
    *,
    variant: ProbeVariant,
    mode: str,
    transcript: str,
    events: list[dict],
) -> dict:
    temp_client = MiniMaxDialogClient(api_key="probe", model=variant.model)
    system_prompt = temp_client._build_plain_reply_system_prompt(mode)
    user_prompt = temp_client._build_plain_reply_user_prompt(
        mode=mode,
        transcript=transcript,
        events=events,
        attempt=0,
    )
    if variant.endpoint_mode == "text_post":
        return {
            "model": variant.model,
            "messages": [
                {"role": "system", "name": "MiniMax AI", "content": system_prompt},
                {"role": "user", "name": "用户", "content": user_prompt},
            ],
            "stream": variant.stream,
            "max_completion_tokens": variant.max_output_tokens,
            "temperature": variant.temperature,
            "top_p": variant.top_p,
        }
    return {
        "model": variant.model,
        "max_tokens": variant.max_output_tokens,
        "temperature": variant.temperature,
        "top_p": variant.top_p,
        "stream": variant.stream,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}],
            }
        ],
    }


def classify_probe_attempt(
    *,
    endpoint_mode: str,
    mode: str,
    transcript: str,
    response: dict | None = None,
    error: str | None = None,
) -> dict:
    if error:
        return {
            "mode": mode,
            "transcript": transcript,
            "status": "request_error",
            "error": error,
            "stop_reason": None,
            "raw_text": "",
            "reply": None,
        }

    if endpoint_mode == "text_post":
        raw_text = _extract_text_from_text_post(response or {})
    else:
        raw_text = _extract_text_from_anthropic(response or {})
    stop_reason = _extract_stop_reason(endpoint_mode, response)
    if not raw_text:
        return {
            "mode": mode,
            "transcript": transcript,
            "status": "no_text",
            "error": None,
            "stop_reason": stop_reason,
            "raw_text": "",
            "reply": None,
        }

    reply = normalize_reply_payload(
        {
            "source": "minimax_plain_text",
            "content": raw_text,
        },
        default_source="minimax_plain_text",
    )
    status = "plain_text_success"
    if looks_truncated_conversation_reply(reply):
        status = "conversation_truncated"

    return {
        "mode": mode,
        "transcript": transcript,
        "status": status,
        "error": None,
        "stop_reason": stop_reason,
        "raw_text": raw_text,
        "reply": reply,
    }


def summarize_probe_attempts(attempts: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    by_mode: dict[str, dict[str, Any]] = {}
    by_scenario: dict[str, dict[str, Any]] = {}
    by_variant: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        status = attempt["status"]
        mode = attempt["mode"]
        scenario = attempt.get("scenario") or "unknown"
        variant = attempt.get("variant") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        mode_bucket = by_mode.setdefault(mode, {"total": 0, "by_status": {}})
        mode_bucket["total"] += 1
        mode_bucket["by_status"][status] = mode_bucket["by_status"].get(status, 0) + 1
        scenario_bucket = by_scenario.setdefault(scenario, {"total": 0, "mode": mode, "by_status": {}})
        scenario_bucket["total"] += 1
        scenario_bucket["by_status"][status] = scenario_bucket["by_status"].get(status, 0) + 1
        variant_bucket = by_variant.setdefault(variant, {"total": 0, "by_status": {}})
        variant_bucket["total"] += 1
        variant_bucket["by_status"][status] = variant_bucket["by_status"].get(status, 0) + 1
    return {
        "total": len(attempts),
        "by_status": by_status,
        "by_mode": by_mode,
        "by_scenario": by_scenario,
        "by_variant": by_variant,
    }


def _request_args_for_variant(settings_obj: Settings, variant: ProbeVariant) -> tuple[str, dict[str, str], float]:
    if variant.endpoint_mode == "text_post":
        return (
            TEXT_POST_URL,
            {
                "Authorization": f"Bearer {settings_obj.minimax_api_key}",
                "Content-Type": "application/json",
            },
            settings_obj.minimax_text_timeout_seconds,
        )
    return (
        f"{settings_obj.minimax_text_base_url.rstrip('/')}/v1/messages",
        {
            "x-api-key": settings_obj.minimax_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        settings_obj.minimax_text_timeout_seconds,
    )


def run_probe(
    *,
    settings_obj: Settings,
    variants: list[ProbeVariant],
    scenarios: list[ProbeScenario],
    repeats: int,
    output_dir: Path,
) -> dict:
    if not settings_obj.minimax_api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set in the current shell environment")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    attempts_path = output_dir / f"minimax-probe-{timestamp}.jsonl"
    summary_path = output_dir / f"minimax-probe-{timestamp}-summary.json"

    client = MiniMaxDialogClient(
        api_key=settings_obj.minimax_api_key,
        model=settings_obj.minimax_text_model,
        base_url=settings_obj.minimax_text_base_url,
        timeout_seconds=settings_obj.minimax_text_timeout_seconds,
    )

    attempts: list[dict] = []
    for variant in variants:
        for round_index in range(repeats):
            for scenario in scenarios:
                started = time.perf_counter()
                payload = build_probe_payload(
                    variant=variant,
                    mode=scenario.mode,
                    transcript=scenario.transcript,
                    events=scenario.events,
                )
                url, headers, timeout = _request_args_for_variant(settings_obj, variant)
                try:
                    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    response_bytes = client._request_sender(url, body, headers, timeout)
                    response = _parse_streaming_response(variant.endpoint_mode, response_bytes)
                    classified = classify_probe_attempt(
                        endpoint_mode=variant.endpoint_mode,
                        mode=scenario.mode,
                        transcript=scenario.transcript,
                        response=response,
                    )
                    classified["raw_response"] = response
                except Exception as exc:
                    classified = classify_probe_attempt(
                        endpoint_mode=variant.endpoint_mode,
                        mode=scenario.mode,
                        transcript=scenario.transcript,
                        error=str(exc),
                    )
                    classified["raw_response"] = None

                classified["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                classified["round"] = round_index + 1
                classified["scenario"] = scenario.name
                classified["variant"] = variant.name
                classified["endpoint_mode"] = variant.endpoint_mode
                classified["payload"] = payload
                attempts.append(classified)

    with attempts_path.open("w", encoding="utf-8") as handle:
        for attempt in attempts:
            handle.write(json.dumps(attempt, ensure_ascii=False) + "\n")

    summary = summarize_probe_attempts(attempts)
    summary["attempts_path"] = str(attempts_path)
    summary["variants"] = [variant.name for variant in variants]
    summary["variant_count"] = len(variants)
    summary["scenarios"] = len(scenarios)
    summary["repeats"] = repeats
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated MiniMax text-generation probes.")
    parser.add_argument("--repeats", type=int, default=3, help="How many times to run each scenario per variant.")
    parser.add_argument(
        "--output-dir",
        default=".runtime/minimax-probe",
        help="Where to write jsonl attempts and summary json.",
    )
    args = parser.parse_args()

    try:
        summary = run_probe(
            settings_obj=settings,
            variants=default_variants(),
            scenarios=default_scenarios(),
            repeats=max(1, args.repeats),
            output_dir=Path(args.output_dir),
        )
    except Exception as exc:
        print(f"MiniMax probe failed: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
