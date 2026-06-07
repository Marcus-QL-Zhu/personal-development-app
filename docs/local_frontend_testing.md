# Local frontend testing

This setup lets Codex open the app in a local browser and verify front-end
behavior without manually installing an APK.

## Secrets

Keep real provider credentials in the repo-root `.env`. The backend loads it
automatically. Do not put provider API keys in Flutter code or browser
`--dart-define` values.

## Browser demo mode

Use this for fast UI behavior checks. It does not call provider APIs, does not
need the backend, and replaces microphone/TTS/native audio with deterministic
browser test doubles.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_mobile_web.ps1
```

Open:

```text
http://127.0.0.1:7357
```

The entry point is `mobile/lib/main_local.dart`. By default it passes
`DemoGameVoiceRepository` into `GameVoiceApp`.

## Browser against real backend

The current Web HTTP repository is a compile-safe placeholder. Browser mode is
for front-end behavior using `DemoGameVoiceRepository`; it intentionally keeps
provider secrets out of the browser.

For real backend and native audio behavior, use Android/emulator mode below.

## Android/emulator against real backend

Start the backend from `.env`, then run the Android app with an emulator backend
URL:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_backend.ps1
powershell -ExecutionPolicy Bypass -File .\tools\run_mobile_android.ps1
```

Or one command:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_mobile_android.ps1 -StartBackend
```

For a physical phone, pass a LAN backend URL printed by `start_backend.ps1`:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_mobile_android.ps1 -DeviceId <device-id> -BackendUrl http://<lan-ip>:8010
```

## Voice replay against real backend

Use this when you want to test the real voice loop without reinstalling the
phone app. It replays a local audio file into the backend live WebSocket, using
the same realtime ASR and assistant/TTS backend path as the app.

Prerequisites:

- repo-root `.env` contains the provider keys used by backend ASR, dialog, and
  TTS providers
- `ffmpeg` is available on `PATH`
- backend is running on `http://localhost:8010`

Start or restart the backend:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\stop_backend.ps1
powershell -ExecutionPolicy Bypass -File .\tools\start_backend.ps1
```

Run a replay from one of the Windows recorder files:

```powershell
$env:PYTHONUTF8 = "1"
python backend/tools/replay_voice_file.py `
  --input "<path-to-local-audio-file.m4a>" `
  --table-name "Replay Full Intro" `
  --post-final-wait-seconds 30 `
  --drain-tts-stream
```

What it verifies:

- converts the input file to 16 kHz mono PCM WAV
- sends the audio in paced chunks to `/ws/tables/{table_id}/listen`
- waits for ASR transcript/final events
- waits after final for `assistant_ready`
- fetches `/context`, `/live-diagnostics`, `/runtime/events`, and `/tts-jobs`
- optionally drains `/tts-streams/{stream_id}/next` to prove TTS audio chunks are
  available

Each run writes a summary here:

```text
.runtime/replay-voice-file/<timestamp>-<input-name>/summary.json
```

A healthy end-to-end result should have:

- `event_summary.final_count >= 1`
- `event_summary.assistant_ready_count >= 1`
- `event_summary.error_count == 0`
- at least one item in `tts_jobs.jobs`
- at least one drained TTS stream chunk when `--drain-tts-stream` is used

Known good examples from the current environment:

```powershell
python backend/tools/replay_voice_file.py `
  --input "<path-to-local-audio-file.m4a>" `
  --table-name "Replay Full Intro" `
  --post-final-wait-seconds 30 `
  --drain-tts-stream

python backend/tools/replay_voice_file.py `
  --input "<path-to-another-local-audio-file.m4a>" `
  --table-name "Replay Full Sanguosha" `
  --post-final-wait-seconds 45 `
  --drain-tts-stream
```

Notes:

- Browser demo mode still uses test doubles; voice replay is a backend
  integration probe, not a browser microphone test.
- This does not verify Android audio focus, physical speaker routing, or native
  capture during playback. Those still belong on Android/emulator or a real
  device.
- The replay-created tables may have no custom `assistant_voice_id`. Backend TTS
  treats a blank voice id as "use the default voice".

## One command

Demo mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_local_frontend_test_env.ps1
```

## What Codex can verify in the browser

- Main navigation
- Open table flow
- Load history flow
- Table shell context rendering
- Runtime/status UI behavior
- TTS success/failure UI state using browser doubles
- Local diagnostics panels that do not require real native audio

## What still belongs on Android/emulator

- Real microphone permissions and capture
- Native live audio capture bridge
- Android audio focus / communication mode
- Physical playback routing
- Provider-backed realtime ASR behavior
