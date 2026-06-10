# Project Operating Rules

## Source of Truth

- GitHub `main` is the source of truth for code and documentation:
  `https://github.com/Marcus-QL-Zhu/personal-development-app`
- The local workspace at `C:\Users\wande\Documents\Codex_workspace\personal development app` is the working copy.
- The server path `/opt/personal-development-app` on `139.224.164.156` is a deployment copy, not the canonical source.
- Runtime data lives outside source control. Do not commit `.env`, local runtime DBs, uploaded audio, generated caches, or server-only secrets.

## Three-End Sync Rule

When code or app behavior changes, keep these three ends aligned before reporting completion:

1. Local workspace: commit intended changes and confirm `git status --short` is clean.
2. GitHub: push the branch and confirm local `HEAD` matches upstream.
3. Server: deploy changed backend files or rebuild/redeploy the app as needed, restart `personal-development.service`, and verify `/health`.

If the server is not a git checkout, verify deployment by file hash, service status, and a targeted smoke test instead of relying on `git status`.

## Personal Development App Boundaries

- Keep this project independent from `gamevoice-app`; do not copy new changes back into the original game voice project unless explicitly asked.
- App identity must remain `personal development app`, including package/application identifiers.
- Backend port is `8011` unless the user explicitly changes deployment.
- Tencent, MiniMax, and Feishu credentials must come from backend `.env` or server environment. Never hard-code them in source, tests, docs, or generated artifacts.

## Data And Integration Rules

- Feishu Bitable is employee-visible. Do not sync manager-only feedback or private manager notes into Feishu.
- Manager feedback is stored in backend DB and shown in the mobile app only.
- Coaching summaries, action plans, and manager notes must be human-readable plain text. Avoid markdown formatting, JSON/Python dict strings, or code-like fragments in user-facing fields.
- Do not invent action plans or facts. If the transcript does not contain one, say that no clear action plan was formed.
- Audio files are temporary working data. Retain according to the configured retention policy; preserve transcripts and summaries.

## Verification Before Completion

- For backend changes, run the relevant focused tests first, then broader backend tests when the change touches shared behavior.
- For production-affecting backend changes, run a server health check after restart.
- For summarization or formatting changes, use at least one real transcript smoke test when available.
- Do not claim completion from assumptions; report the exact verification commands and results.
