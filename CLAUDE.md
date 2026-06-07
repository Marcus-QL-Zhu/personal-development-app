# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Aweson桌游助手 — an Android/HarmonyOS voice companion app. The backend runs FastAPI (Python) on port 8010; the mobile is Flutter.

**Current division of labor:**
- `backend/` — Codex owns
- `mobile/` — this workspace owns (Flutter/Android frontend)

## Commands

### Backend
```bash
cd backend
pytest                           # Run all tests (exclude slow realtime tests)
pytest tests/ -k "not test_realtime"   # Skip realtime tests
powershell -ExecutionPolicy Bypass -File tools/start_backend.ps1   # Start server
powershell -ExecutionPolicy Bypass -File tools/smoke_backend.ps1  # Smoke test
```

### Mobile
```bash
cd mobile
flutter analyze                  # Lint
flutter test                     # Run tests
flutter build apk --debug        # Build debug APK
flutter build apk --release      # Build release APK
```

## Architecture

### Backend — FastAPI + uvicorn

Key modules in `backend/src/gamevoice_server/`:
- `main.py` — FastAPI app entry, all REST/WebSocket endpoints, runtime event kinds
- `session_manager.py` — In-memory per-table session state (messages, runtime_events, speaker_identities, compaction state)
- `companion_orchestrator.py` — Main reply planning; routes to turn_decision, dialog_client, rules_router
- `turn_decision.py` — Local rules + lightweight state machine for millisecond-level turn decisions (NOT LLM-blocking)
- `dialog_client.py` — MiniMax text API client (`MiniMax-M2.7-highspeed`)
- `tts_adapter.py` — MiniMax TTS WebSocket (`speech-2.8-hd`)
- `tencent_realtime_asr.py` — Tencent Cloud real-time ASR WebSocket bridge
- `memory_compactor.py` — Context compaction (triggers at ~40k tokens, produces narrative summary)
- `rules_router.py` / `rules_index.py` — Local rules lookup for serious-mode routing

External dependencies: Tencent Cloud ASR, MiniMax API (text + TTS).

### Mobile — Flutter

Key files in `mobile/lib/`:
- `main.dart` — App entry, backend URL configured by `GAMEVOICE_BACKEND_URL`
- `screens/table_shell.dart` — Main stateful UI screen (~62KB)
- `backend/http_gamevoice_repository.dart` — REST API calls
- `live/live_transcription_client.dart` — WebSocket for real-time ASR transcripts
- `audio/voice_recorder.dart` — Live audio capture (native AudioRecord on HarmonyOS/Android)
- `audio/duplex_audio_session.dart` — Native duplex audio bridge
- `tts/tts_audio_player.dart` — Streams TTS audio from backend

Communication: REST for table management, WebSocket (`/ws/tables/{table_id}/listen`) for live audio streaming.

## Key Design Decisions

- **Turn decision is local rules + state machine** — never blocks on LLM network round-trip
- **Append-only main event stream** — runtime events (TTS job states, etc.) do NOT enter main context
- **`lead / content / tail` are runtime generation strategies** — not persisted history structures
- **Preview → formal handoff** — preview lead speaks first, formal content continues via plain continuation text (not JSON regeneration)
- **Barge-in**: partial/stable ASR triggers interruption while assistant is speaking; only final transcripts enter history
- **Async rule analysis**: serious rule queries run in subprocess; results flow back as natural rejoinder, never raw
- **Memory compaction**: when context exceeds ~40k tokens, background compaction produces narrative summary; active view becomes `summary + checkpoint_tail`
- **Assistant name** (default `宝子`): configured per-table before session starts, frozen after; used for turn triggering
- **Speaker identity**: anonymous `player_a/b/c/d` buckets → optional name linking (`玩家A（马斯克）`)

## Voice IDs

Voice IDs are provider-account-bound deployment values. Keep real voice IDs in
private `.env` files or private mobile configuration, not in this public repo.

## Important File Paths

- Backend entry: `backend/src/gamevoice_server/main.py`
- Mobile entry: `mobile/lib/main.dart`
- Spec: `SPEC.md`
- Backend tests: `backend/tests/`
- Mobile tests: `mobile/test/`
- Backend tools (start/stop/smoke): `tools/`

## API Base

Backend runs on port **8010**. Mobile reads the backend URL from
`GAMEVOICE_BACKEND_URL` when built, with local defaults for development.

## Parallel Development Rules

This project may use Git worktrees for parallel development. Keep worktree paths
local to each contributor and do not commit machine-specific paths.

### Workspaces

| Workspace | Branch | Responsible for |
|---|---|---|
| Main integration | `main` or current integration branch | Integration, manual acceptance, final merge |
| Backend feature worktree | `codex/<topic>` | `backend/*`, backend tests, interface contracts, `SPEC.md` |
| Frontend feature worktree | `codex/<topic>` or contributor branch | `mobile/*`, mobile tests, frontend interaction & playback |

### Modification Boundaries

**Backend owns:** `backend/*`, backend tests, interface contracts
**Frontend owns:** `mobile/*`, mobile tests, frontend interaction
**Shared (high-risk):** API response schemas, WebSocket event schemas, runtime state fields, `SPEC.md`

### Shared Changelog

For multi-worktree development, keep a local handoff/changelog outside the
public repository. Entry should include: time, author, branch, scope, affected
files, interface impact, verification method, and next handoff.

### Principles

- No two agents work in the same directory simultaneously
- No two agents modify the same file without communication
- Cross-boundary changes require changelog registration first
- Small, single-purpose commits with clear scope
- Do not refactor files unrelated to the current task
- **Never** use `git reset --hard` or `git checkout -- <file>` — these erase uncommitted work

### Agent Responsibilities

- **Codex**: backend mainline and architecture evolution
- **Claude**: frontend mainline and interaction experience
- If frontend task needs backend changes: log changelog, specify interface impact, then implement
- If backend task affects frontend: log changelog, specify field changes, then implement

### Git Rules

- One feature/fix per branch
- Each commit does exactly one thing
- Do not start parallel tasks on a dirty working tree
- Before merging: local tests pass, shared changelog updated, interface changes communicated to the other side
