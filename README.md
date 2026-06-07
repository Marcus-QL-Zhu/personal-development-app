# Personal Development App

Personal Development App is a private coaching journal for people managers. It records coaching conversations, transcribes uploaded audio, creates detailed coaching summaries, stores employee development history, and syncs shared coaching records to Feishu Bitable.

The mobile app is built with Flutter. The backend is a FastAPI service that integrates with Tencent Cloud ASR, MiniMax reasoning models, and Feishu Bitable.

## Features

- Create consultant/employee profiles with name, optional Gallup StrengthsFinder ranking, and a natural-language profile note.
- Record coaching sessions from the mobile app and confirm before upload.
- Transcribe uploaded audio with Tencent Cloud ASR.
- Generate structured session records with date, topic, detailed summary, and action plan.
- Generate manager-only coaching feedback with MiniMax M3, including strengths, gaps, improvement suggestions, and inferred employee feelings.
- Sync transparent coaching records to Feishu Bitable while keeping manager-only feedback out of Feishu.
- Retain raw audio for a configurable period, defaulting to 90 days.

## Project Layout

```text
backend/   FastAPI backend and tests
mobile/    Flutter mobile app
docs/      Product specs and validation notes
tools/     Local helper scripts
```

## Configuration

Copy `.env.example` to `.env` and fill in the provider credentials you use.

Required integrations for full production behavior:

- Tencent Cloud ASR
- MiniMax API
- Feishu app credentials and Bitable access

Never commit real `.env` files, runtime databases, audio uploads, generated APKs, or provider credentials.

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python -m pytest
python -m uvicorn --app-dir src gamevoice_server.main:app --host 0.0.0.0 --port 8011
```

## Mobile

```bash
cd mobile
flutter pub get
flutter test
flutter build apk --release --dart-define=GAMEVOICE_BACKEND_URL=http://YOUR_SERVER:8011
```

The Android package name is `com.marcus.personaldevelopment`.

## License

MIT. See `LICENSE`.
