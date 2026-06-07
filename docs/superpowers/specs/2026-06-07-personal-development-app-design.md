# Personal Development App Design

## Goal

Build a single-user manager workbench for recording coaching conversations with two new employees, turning each uploaded recording into a detailed employee-facing coaching summary and action plan, storing the full record in the server database, and appending the employee-visible record to that employee's Feishu Bitable.

This is a full-scope implementation, not a staged MVP. Engineering may be sequenced for safety, but the intended delivered product includes backend, mobile UI, Feishu sync, ASR, MiniMax M3 generation, and validation tooling.

## Existing Project Reuse

The work builds on `gamevoice-app`.

- Keep the FastAPI backend on port `8010`.
- Reuse existing backend authentication through `GAMEVOICE_PUBLIC_API_TOKEN`.
- Reuse existing SQLite runtime persistence under `GAMEVOICE_DB_PATH`.
- Reuse the existing mobile recording capability, but use clip recording and upload rather than realtime WebSocket audio.
- Reuse the virtual validation/probe style already present in `.runtime/skillagent-virtual-validation` and backend replay tooling, adapted for post-recording uploads.
- Reuse the server-side Feishu Bitable patterns from `/home/admin/.openclaw/workspace/skills/web-ad-radar/scripts/radar/bitable/`: tenant token refresh, record creation, retry/backoff, and date handling.

## Product Shape

The app is for the manager only. Employees mainly consume the resulting Feishu records. There is no employee login, no multi-manager user system, and no formal account system.

Primary flow:

1. Manager creates or edits an employee profile.
2. Manager selects an employee.
3. Manager starts recording a coach session.
4. Manager stops recording.
5. App asks exactly one manual confirmation: whether to upload this recording.
6. After confirmation, all downstream processing is automatic.
7. Server saves audio, transcribes it, generates summaries, stores DB records, and appends to Feishu.

No extra manual confirmation is required before Feishu sync. If quality is poor, the record is still synced with a quality warning.

## Employee Profile

Fields:

- `name`: required.
- `gallup_raw`: pasted Gallup Strength Finder 34 ranking text.
- `gallup_strengths`: parsed structured ranking.
- `profile_note`: natural-language background note, editable anytime.
- `feishu`: binding information for that employee's Feishu Bitable.

Gallup handling:

- User pastes text such as `1 Learner\n2 Strategic`.
- System parses numbered ranks into structured `{rank, name}` items.
- Gallup does not enter Feishu records.
- Gallup does not enter employee-facing coaching summaries.
- Gallup may be used only in local app `manager_feedback`.

The profile note is light background context only. It should not override the current coach transcript.

## Feishu Bitable

Each employee gets a separate Feishu Bitable destination. The system creates and initializes the destination when the employee profile is created. The manager manually shares the Feishu destination with the employee; the system does not auto-share permissions.

Default structure should prioritize safe sharing. If a single base with per-employee table cannot support safe table-level sharing, use one independent base per employee.

Each coach upload appends one record. Multiple coach sessions on the same date are not merged.

Fields:

- `日期`
- `主题`
- `内容总结`
- `Action Plan`
- `质量状态`
- `本地记录ID`

Feishu must not receive:

- Full transcript.
- Raw recording.
- Manager feedback.
- Gallup raw text or parsed strengths.
- Private sync/debug data.

If ASR or generation quality is weak, Feishu still receives the record, with `质量状态` marked accordingly.

## Audio And ASR

Realtime ASR is not needed for this product. The app records a normal clip and uploads it after the manager confirms.

Recording assumptions:

- Ordinary phone single-channel recording.
- Manager and employee sit in stable positions with the phone between them.
- Conversation is usually two speakers.

ASR recommendation:

- Primary: Tencent Cloud recording file recognition flash API, because it supports HTTPS POST upload and synchronous fast return.
- Enable speaker diarization where supported.
- Fallback: Tencent Cloud async recording file recognition if flash API limits are exceeded or flash quality/feature support is insufficient.

The system automatically infers speaker roles (manager vs employee). It does not ask the user to confirm or correct speaker mapping. If the inference is wrong, the product accepts that tradeoff to keep operation cost low.

Retention:

- Raw audio is retained for 90 days, then cleaned.
- Full transcript is retained permanently in server DB.

## AI Generation

Use MiniMax M3 reasoning with the existing MiniMax API key:

```text
MINIMAX_REASONING_MODEL=MiniMax-M3
MINIMAX_REASONING_BASE_URL=https://api.minimaxi.com/v1/chat/completions
MINIMAX_REASONING_ENABLED=true
MINIMAX_REASONING_THINKING_TYPE=adaptive
MINIMAX_REASONING_SPLIT=true
```

M3 is a thinking model and may use a different request/response shape from the existing M2.7 highspeed flow. Implementation must include a provider probe or request-shape test before relying on it.

Default output language is Chinese.

Employee-facing generated fields:

- `主题`: generated from the summary/action content, not entered before recording.
- `内容总结`: detailed, structured, and knowledge-complete. It must not be a vague short recap.
- `Action Plan`: faithful to the recording. Record only actions actually discussed. Do not invent deadlines, deliverables, or acceptance criteria. If none exist, write `本次未形成明确 Action Plan。`

Employee-facing summary should cover:

- Knowledge points taught by the manager.
- Feedback and corrections given to the employee.
- Key examples or caution points.
- Explicit action items, if any.

Manager-only generated field:

- `manager_feedback`: stored in DB and shown in mobile app only. Never sync to Feishu.

Manager feedback should evaluate:

- Knowledge clarity.
- Whether the communication matched the employee context and Gallup profile.
- Action plan clarity.
- Rhythm and pacing.
- Interaction quality.
- Employee feeling inference.

Employee feeling inference must use an evidence chain rather than pretend certainty:

- observed words or behavior,
- possible feeling,
- why this is inferred,
- how the manager can confirm or adjust next time.

Manager feedback may quote both manager and employee lines as evidence.

## Data Model

Server DB stores:

- Employees.
- Gallup raw and parsed strengths.
- Profile note.
- Feishu binding.
- Coach sessions.
- Raw audio path and expiry.
- Full transcript and speaker segments.
- Employee-facing summary/action plan.
- Manager-only feedback.
- Feishu sync state.

Feishu stores only the public employee-facing subset.

## API Shape

Development routes live under `/development/*` and coexist with old GameVoice routes.

Expected endpoints:

- `GET /development/employees`
- `POST /development/employees`
- `GET /development/employees/{employee_id}`
- `PUT /development/employees/{employee_id}`
- `GET /development/employees/{employee_id}/coaching-sessions`
- `POST /development/employees/{employee_id}/coaching-sessions`

The upload endpoint accepts multipart field `clip`.

## Mobile UI

The first screen should reuse the old first-level menu structure, but the menu semantics become the personal-development workflow:

- `设定助手` becomes `新增顾问`.
- `开桌` becomes `编辑履历`.
- `加载历史` becomes `coach历史`.
- `调试功能` remains `调试功能`.

The app is a utilitarian manager tool. It should not rely on the old TTS, realtime listening, tabletop rules, or assistant persona flows.

### 新增顾问

The `新增顾问` page creates a new employee/consultant profile.

Fields:

- `名称`: required.
- `介绍`: optional natural-language profile note.
- `Gallup`: optional pasted Gallup Strength Finder ranking.

Form behavior:

- The name field must reject empty values.
- The profile note and Gallup fields must have explicit max lengths.
- Long text fields must have fixed heights, internal vertical scrolling, and visible scroll affordance where the platform supports it, so long content does not stretch the page uncontrollably.
- A bottom `确认保存` button saves the profile.
- After successful save, the app must automatically navigate to that consultant's edit profile screen.
- The edit profile screen must show the consultant's Feishu Bitable link in a read-only field with a copy button on the right, so the manager can send the link to the consultant.
- After successful save, the new consultant must appear in both `编辑履历` and `coach历史`.

### 编辑履历

The `编辑履历` page first shows the existing consultants. Tapping a consultant opens an edit page using the same form shape as `新增顾问`.

Behavior:

- Name, profile note, and Gallup can be changed.
- The same required-name validation and long-text constraints apply.
- A bottom `确认保存` button saves changes to the backend.
- The edit profile screen shows the consultant's Feishu Bitable link when the backend has returned one. The link is read-only and has a right-side copy action.
- After successful save, the updated consultant name/details must be reflected in consultant lists and future coaching generation context.

### coach历史

The `coach历史` page first shows the existing consultants. Tapping a consultant opens that consultant's coaching history.

History list behavior:

- The list belongs to one selected consultant only; records from different consultants are not mixed.
- Each list row shows only date and topic by default, plus compact status indicators for quality/sync where useful.
- Records are sorted in reverse chronological order, with the newest coach record at the top.
- The history list must be vertically scrollable so large histories do not trap or overflow the page.
- A bottom fixed `开始录音` button remains reachable even when the history list is long.

Recording behavior:

- Tapping `开始录音` starts recording for the selected consultant.
- The page must show an obvious recording state: red recording indicator, elapsed timer, and a clear motion/level/pulse affordance.
- While recording, the primary button changes to `结束录音`.
- Tapping `结束录音` stops the clip and shows the single required confirmation dialog asking whether to upload.
- If confirmed, upload and all downstream processing are automatic.
- While upload/ASR/generation/sync are running, show a processing state so the user does not think the app is stuck.

### coach详情

Tapping a coach history row opens a detail page.

The detail page shows:

- Date, topic, quality status, and Feishu sync status.
- `内容总结`.
- `Action Plan`.
- `Manager Notes`, which is the manager-only LLM coach-the-coach feedback.
- Full transcript in a collapsed or secondary section, not expanded by default.

Manager Notes and full transcript remain app/DB-only and must not be synced to Feishu.

## Validation

Automated tests should use fake providers:

- Fake Tencent ASR.
- Fake MiniMax M3.
- Fake Feishu Bitable.

Real provider probes may be added but must not require committed secrets.

Validation audio:

- High math lecture, single speaker, first 10 minutes:
  `https://www.bilibili.com/video/BV1JK4y1e7Ue/?vd_source=74bdd2d836d0455e9eaf5ee0ebc4f6f2`
- Two-person interview, first 10 minutes:
  `https://www.bilibili.com/video/BV1PYEF6xE2B/?spm_id_from=333.337.search-card.all.click&vd_source=74bdd2d836d0455e9eaf5ee0ebc4f6f2`

Validation tooling should download or accept prepared audio clips, trim/extract the first 10 minutes, then run the same upload/post-processing path used by the app.

## Non-Goals

- Employee mobile login.
- Realtime ASR UX.
- TTS playback.
- Auto-sharing Feishu permissions.
- Storing full transcript in Feishu.
- Storing manager feedback in Feishu.
- Manual review before Feishu sync.
- Speaker correction UI.
