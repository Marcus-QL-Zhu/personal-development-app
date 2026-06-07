# Local backend helpers

Use these scripts from the repo root to make mobile/backend local testing simpler.

## Optional Tencent ASR configuration

If you want `/tables/{table_id}/audio-clips` to use real Tencent Cloud sentence recognition instead of the local placeholder transcript, set these environment variables before starting the backend:

```powershell
$env:TENCENT_APP_ID = "your-app-id"
$env:TENCENT_SECRET_ID = "your-secret-id"
$env:TENCENT_SECRET_KEY = "your-secret-key"
$env:TENCENT_ASR_REGION = "ap-shanghai"
```

Optional:

```powershell
$env:TENCENT_ASR_ENGINE = "16k_zh"
$env:TENCENT_ASR_TIMEOUT_SECONDS = "10"
$env:TENCENT_REALTIME_ENGINE = "16k_zh"
$env:TENCENT_REALTIME_NEED_VAD = "1"
$env:TENCENT_REALTIME_VOICE_FORMAT = "1"
$env:TENCENT_REALTIME_CHUNK_BYTES = "6400"
```

Optional live silence gate:

```powershell
$env:LIVE_SILENCE_GATE_ENABLED = "1"
$env:LIVE_SILENCE_GATE_VAD_MODE = "1"
```

The backend uses WebRTC VAD when available. `backend/pyproject.toml` uses the `webrtcvad-wheels` package so Windows development machines can load the native module without local C++ build tools. If the native VAD module is not installed or cannot load, the realtime audio bridge fails open and forwards all audio unchanged.

If the Tencent credentials are not present, the backend falls back to the placeholder transcript response so the mobile flow can still be tested end to end.

For the new live listening flow, the backend now also exposes a WebSocket bridge at:

```text
/ws/tables/{table_id}/listen
```

This bridge forwards mobile microphone PCM chunks to Tencent Cloud realtime ASR and streams transcript events back to the phone.

## Start the backend

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_backend.ps1
```

This starts the FastAPI backend in the background, writes a pid file to `.runtime\backend.pid`, and prints:

- the Android emulator URL: `http://10.0.2.2:8010`
- one or more LAN URLs for a physical device
- log file locations under `.runtime\`

## Check backend status

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\backend_status.ps1
```

## Run a smoke test

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\smoke_backend.ps1
```

This runs:

- `GET /health`
- `POST /tables`
- file upload into the document space
- summary readback

## Replay a recorded voice file through live ASR and TTS

Use this to verify the real backend voice loop from a local `.m4a`/audio file
without installing a new APK:

```powershell
$env:PYTHONUTF8 = "1"
python backend/tools/replay_voice_file.py `
  --input "<path-to-local-audio-file.m4a>" `
  --table-name "Replay Full Intro" `
  --post-final-wait-seconds 30 `
  --drain-tts-stream
```

The probe writes a JSON summary under `.runtime\replay-voice-file\...` and
checks the path:

- local audio file -> paced WebSocket chunks
- Tencent realtime ASR transcript/final events
- assistant `assistant_ready`
- backend TTS job creation
- TTS stream chunk availability

More detail: `docs/local_frontend_testing.md`.

## Stop the backend

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\stop_backend.ps1
```

## Start the browser front-end test shell

For quick UI behavior testing without installing an APK:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_mobile_web.ps1
```

This opens the Flutter Web local entry point with the demo repository enabled.
Codex can then inspect `http://127.0.0.1:7357` in the browser.

More detail: `docs/local_frontend_testing.md`.

## Run Android/emulator against the real backend

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_mobile_android.ps1 -StartBackend
```

This uses `GAMEVOICE_BACKEND_URL=http://10.0.2.2:8010` for the Android emulator.
For a physical device, pass the LAN URL printed by `start_backend.ps1`.
