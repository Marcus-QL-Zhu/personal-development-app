# Parallel Coaching Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split employee-visible coaching summaries and manager-only coach feedback into two focused MiniMax M3 requests that run in parallel, then merge, validate, format, store, and sync them through the existing service contract.

**Architecture:** Keep `CoachingInsightGeneratorPort.generate(employee, transcript) -> dict[str, str]` unchanged so `PersonalDevelopmentService` and mobile clients do not need schema changes. Inside `MiniMaxM3CoachingInsightGenerator`, fan out into two independent MiniMax requests: an employee-summary branch returning `topic/content_summary/action_plan`, and a manager-feedback branch returning `manager_feedback`; merge the formatted outputs before returning. Preserve existing repair/retry/formatter behavior and add branch-specific validation so Feishu only receives employee-visible fields.

**Tech Stack:** Python 3.11, FastAPI backend, SQLite store, MiniMax Chat Completions API, pytest, existing Feishu append flow.

---

## Files

- Modify: `backend/src/gamevoice_server/personal_development.py`
  - Add branch-specific prompt builders.
  - Add branch-specific validation helpers.
  - Add parallel execution inside `MiniMaxM3CoachingInsightGenerator.generate`.
  - Keep public generator return shape unchanged.
- Modify: `backend/tests/test_personal_development_minimax_robustness.py`
  - Add tests for two MiniMax calls, prompt separation, branch merge, and branch repair.
- Modify: `backend/tests/test_personal_development.py`
  - Update existing payload-count assumptions for `test_minimax_m3_generator_sends_reasoning_payload_and_parses_json`.
- Create or update ignored local test artifacts only under `/tmp` on the server for smoke testing; do not commit smoke scripts.

---

### Task 1: Fan Out Employee Summary And Manager Feedback Requests

**Files:**
- Modify: `backend/src/gamevoice_server/personal_development.py`
- Test: `backend/tests/test_personal_development_minimax_robustness.py`

- [ ] **Step 1: Write the failing test**

Append this test to `backend/tests/test_personal_development_minimax_robustness.py`:

```python
def test_minimax_m3_generator_runs_employee_and_manager_prompts_separately():
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        prompt = json["messages"][0]["content"]
        if "employee_visible_summary" in prompt:
            return FakeResponse(
                '{"topic":"SRE JD training",'
                '"content_summary":"Covered JD keywords, cloud platforms, CI/CD, and monitoring.",'
                '"action_plan":"1. Review SLI/SLO/SLA.\\n2. Stop using Liepin for this search."}'
            )
        if "manager_only_feedback" in prompt:
            return FakeResponse(
                '{"manager_feedback":"整体观察：The coach used repeated questioning.\\n\\n'
                '讲解清晰度：Good examples, but too fast."}'
            )
        raise AssertionError(prompt)

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
        retry_delay_seconds=0,
    )

    result = generator.generate(
        employee={
            "name": "Crany",
            "profile_note": "new consultant",
            "gallup_strengths": [{"rank": 1, "name": "Input"}],
        },
        transcript={"text": "coach transcript", "segments": []},
    )

    assert len(calls) == 2
    prompts = [call["messages"][0]["content"] for call in calls]
    assert any("employee_visible_summary" in prompt for prompt in prompts)
    assert any("manager_only_feedback" in prompt for prompt in prompts)
    employee_prompt = next(prompt for prompt in prompts if "employee_visible_summary" in prompt)
    manager_prompt = next(prompt for prompt in prompts if "manager_only_feedback" in prompt)
    assert "gallup_strengths_for_manager_feedback_only" not in employee_prompt
    assert "manager_feedback" not in employee_prompt
    assert "Gallup" in manager_prompt
    assert result == {
        "topic": "SRE JD training",
        "content_summary": "Covered JD keywords, cloud platforms, CI/CD, and monitoring.",
        "action_plan": "1. Review SLI/SLO/SLA.\n2. Stop using Liepin for this search.",
        "manager_feedback": "【整体观察】\nThe coach used repeated questioning.\n\n【讲解清晰度】\nGood examples, but too fast.",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_personal_development_minimax_robustness.py::test_minimax_m3_generator_runs_employee_and_manager_prompts_separately -q
```

Expected: FAIL because the current generator sends one combined prompt and returns all fields from one response.

- [ ] **Step 3: Implement branch prompt builders and parallel send**

In `MiniMaxM3CoachingInsightGenerator`:

1. Import `ThreadPoolExecutor` near other imports:

```python
from concurrent.futures import ThreadPoolExecutor
```

2. Replace `generate()` internals with a two-branch implementation:

```python
def generate(self, *, employee: dict[str, Any], transcript: dict[str, Any]) -> dict[str, str]:
    employee_prompt = self._build_employee_summary_prompt(employee=employee, transcript=transcript)
    manager_prompt = self._build_manager_feedback_prompt(employee=employee, transcript=transcript)
    with ThreadPoolExecutor(max_workers=2) as executor:
        employee_future = executor.submit(
            self._generate_branch,
            prompt=employee_prompt,
            validation_fn=_employee_summary_validation_errors,
            branch_name="employee summary",
        )
        manager_future = executor.submit(
            self._generate_branch,
            prompt=manager_prompt,
            validation_fn=_manager_feedback_validation_errors,
            branch_name="manager feedback",
        )
        employee_result = employee_future.result()
        manager_result = manager_future.result()
    return _format_coaching_generation({**employee_result, **manager_result})
```

3. Add `_generate_branch()`:

```python
def _generate_branch(self, *, prompt: str, validation_fn: Any, branch_name: str) -> dict[str, Any]:
    body = self._send_payload(self._build_payload(prompt))
    content = self._extract_message_content(body)
    parsed = self._parse_or_repair_content(content)
    validation_errors = validation_fn(parsed)
    if validation_errors:
        regenerated_content = self._regenerate_complete_json(
            original_prompt=prompt,
            invalid_content=content,
            validation_errors=validation_errors,
        )
        parsed = self._parse_or_repair_content(regenerated_content)
        validation_errors = validation_fn(parsed)
    if validation_errors:
        raise MiniMaxM3Error(f"MiniMax M3 returned incomplete {branch_name}: {validation_errors}")
    return dict(parsed)
```

4. Add `_build_employee_summary_prompt()`:

```python
@staticmethod
def _build_employee_summary_prompt(*, employee: dict[str, Any], transcript: dict[str, Any]) -> str:
    payload = {
        "task": "employee_visible_summary",
        "language": "Chinese by default",
        "employee": {
            "name": employee.get("name", ""),
            "profile_note": employee.get("profile_note", ""),
        },
        "transcript": {
            "text": transcript.get("text", ""),
            "segments": transcript.get("segments", []),
        },
        "requirements": [
            "Only generate employee-visible coaching notes.",
            "Return one JSON object with exactly these fields: topic, content_summary, action_plan.",
            "content_summary must cover knowledge points, feedback points, key examples, and mistakes to avoid; do not over-compress.",
            "action_plan only records concrete actions, owners, deadlines, deliverables, or acceptance criteria explicitly mentioned in the transcript.",
            "If no action plan is present, write 本次未形成明确 Action Plan。",
            "Do not mention Gallup, manager-only feedback, manager coaching advice, or private manager notes.",
            "Use plain text. Do not output markdown, code fences, HTML, Python dict/list strings, or JSON inside field values.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
```

5. Add `_build_manager_feedback_prompt()`:

```python
@staticmethod
def _build_manager_feedback_prompt(*, employee: dict[str, Any], transcript: dict[str, Any]) -> str:
    payload = {
        "task": "manager_only_feedback",
        "language": "Chinese by default",
        "employee": {
            "name": employee.get("name", ""),
            "profile_note": employee.get("profile_note", ""),
            "gallup_strengths_for_manager_feedback_only": employee.get("gallup_strengths", []),
        },
        "transcript": {
            "text": transcript.get("text", ""),
            "segments": transcript.get("segments", []),
        },
        "requirements": [
            "Only generate manager-only feedback for the manager; this content must not be employee-visible or synced to Feishu.",
            "Return one JSON object with exactly this field: manager_feedback.",
            "manager_feedback must evaluate explanation clarity, Gallup communication fit, action-item clarity, pacing, interaction quality, improvement suggestions, and inferred employee feelings based on evidence.",
            "Use plain text section labels such as 整体观察：, 讲解清晰度：, Gallup 沟通适配：, 行动项清晰度：, 节奏：, 互动质量：, 员工感受推测：, 改进建议：.",
            "Do not generate topic, content_summary, or action_plan.",
            "Use plain text. Do not output markdown, code fences, HTML, Python dict/list strings, or JSON inside field values.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_personal_development_minimax_robustness.py::test_minimax_m3_generator_runs_employee_and_manager_prompts_separately -q
```

Expected: PASS.

---

### Task 2: Add Branch-Specific Validation And Update Existing Tests

**Files:**
- Modify: `backend/src/gamevoice_server/personal_development.py`
- Modify: `backend/tests/test_personal_development.py`
- Modify: `backend/tests/test_personal_development_minimax_robustness.py`

- [ ] **Step 1: Write failing validation tests**

Add these tests to `backend/tests/test_personal_development_minimax_robustness.py`:

```python
def test_employee_summary_branch_regenerates_without_requiring_manager_feedback():
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        prompt = json["messages"][0]["content"]
        if "manager_only_feedback" in prompt:
            return FakeResponse('{"manager_feedback":"整体观察：Manager stayed practical."}')
        if "validation_errors" in prompt:
            return FakeResponse(
                '{"topic":"Search channel training",'
                '"content_summary":"Explained LinkedIn search, referrals, and daily news standards.",'
                '"action_plan":"1. Move search to LinkedIn."}'
            )
        return FakeResponse('{"topic":"待提炼","content_summary":"","action_plan":""}')

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
        retry_delay_seconds=0,
    )

    result = generator.generate(employee={"name": "Crany"}, transcript={"text": "coach", "segments": []})

    assert len(calls) == 3
    assert result["topic"] == "Search channel training"
    assert result["content_summary"]
    assert result["action_plan"] == "1. Move search to LinkedIn."
    assert result["manager_feedback"] == "整体观察：Manager stayed practical."


def test_manager_feedback_branch_regenerates_without_requiring_summary_fields():
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        prompt = json["messages"][0]["content"]
        if "employee_visible_summary" in prompt:
            return FakeResponse(
                '{"topic":"ATS training","content_summary":"Covered registration.","action_plan":"本次未形成明确 Action Plan。"}'
            )
        if "validation_errors" in prompt:
            return FakeResponse('{"manager_feedback":"讲解清晰度：Clear flow with concrete examples."}')
        return FakeResponse('{"manager_feedback":""}')

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
        retry_delay_seconds=0,
    )

    result = generator.generate(employee={"name": "Crany"}, transcript={"text": "coach", "segments": []})

    assert len(calls) == 3
    assert result["topic"] == "ATS training"
    assert result["content_summary"] == "Covered registration."
    assert result["manager_feedback"] == "讲解清晰度：Clear flow with concrete examples."
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_personal_development_minimax_robustness.py::test_employee_summary_branch_regenerates_without_requiring_manager_feedback tests/test_personal_development_minimax_robustness.py::test_manager_feedback_branch_regenerates_without_requiring_summary_fields -q
```

Expected: FAIL until branch validation helpers are implemented.

- [ ] **Step 3: Implement validation helpers**

In `backend/src/gamevoice_server/personal_development.py`, add:

```python
def _employee_summary_validation_errors(parsed: dict[str, Any]) -> list[str]:
    formatted = _format_coaching_generation({**parsed, "manager_feedback": "placeholder"})
    errors: list[str] = []
    if formatted["topic"].strip().lower() in {"", "待提炼", "pending review", "未提供录音内容"}:
        errors.append("topic is placeholder")
    if not formatted["content_summary"].strip():
        errors.append("content_summary is empty")
    return errors


def _manager_feedback_validation_errors(parsed: dict[str, Any]) -> list[str]:
    formatted = _format_coaching_generation(
        {
            "topic": "placeholder",
            "content_summary": "placeholder",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": parsed.get("manager_feedback"),
        }
    )
    errors: list[str] = []
    if not formatted["manager_feedback"].strip():
        errors.append("manager_feedback is empty")
    return errors
```

Update `_repair_json_content()` instructions to allow branch-specific objects by changing:

```python
"The JSON object must contain topic, content_summary, action_plan, manager_feedback.",
```

to:

```python
"The JSON object must preserve the fields requested by the source prompt.",
```

Update `_regenerate_complete_json()` instructions to remove the hard-coded requirement that both summary and manager feedback must be present in one object, and use:

```python
"Return only one valid JSON object.",
"Do not use markdown fences.",
"Use the original transcript and employee context.",
"Regenerate only the fields requested by the original_request_json task.",
"Required fields must be non-empty and evidence-based.",
"If action_plan is requested, it may say 本次未形成明确 Action Plan。 only when the transcript contains no concrete action plan.",
```

- [ ] **Step 4: Update the existing single-call test**

In `backend/tests/test_personal_development.py`, update `test_minimax_m3_generator_sends_reasoning_payload_and_parses_json` so fake responses branch on prompt content:

```python
def fake_post(url, headers, json, timeout):
    captured.setdefault("calls", []).append(json)
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    prompt = json["messages"][0]["content"]
    if "employee_visible_summary" in prompt:
        return FakeResponse(
            '{"topic":"客户需求澄清","content_summary":"知识点：先复述目标。","action_plan":""}'
        )
    return FakeResponse('{"manager_feedback":"节奏评分：好。"}')
```

Then assert:

```python
assert len(captured["calls"]) == 2
prompts = [call["messages"][0]["content"] for call in captured["calls"]]
assert any("employee_visible_summary" in prompt for prompt in prompts)
assert any("manager_only_feedback" in prompt for prompt in prompts)
```

- [ ] **Step 5: Run related tests**

Run:

```powershell
cd backend
python -m pytest tests/test_personal_development_minimax_robustness.py tests/test_personal_development.py tests/test_personal_development_validation_tool.py -q
```

Expected: PASS.

---

### Task 3: Smoke Test, Deploy, And Commit

**Files:**
- Modify: committed files from Tasks 1-2 only.
- Server temp scripts: `/tmp/pd_parallel_generation_smoke.py`.

- [ ] **Step 1: Run full backend tests**

Run:

```powershell
cd backend
python -m pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Deploy backend file to server**

Run:

```powershell
scp "C:\Users\wande\Documents\Codex_workspace\personal development app\backend\src\gamevoice_server\personal_development.py" admin@139.224.164.156:/opt/personal-development-app/backend/src/gamevoice_server/personal_development.py
ssh admin@139.224.164.156 "sudo systemctl restart personal-development.service && sleep 2 && systemctl is-active personal-development.service && curl -sS http://127.0.0.1:8011/health"
```

Expected: `active` and `{"status":"ok"}`.

- [ ] **Step 3: Smoke test with today’s real Crany transcript without writing DB**

Create a temporary server script that:

1. Opens `/opt/personal-development-app/.runtime/personal-development.db`.
2. Loads session `eaca593c-82d6-4dce-9ea3-51e663e9f0e8`.
3. Loads the employee record.
4. Instantiates the production `MiniMaxM3CoachingInsightGenerator` from `.env` settings.
5. Calls `generate(employee=employee, transcript={"text": session["transcript_text"], "segments": session["speaker_segments"]})`.
6. Prints field lengths, newline counts, and whether `manager_feedback` has bracket sections.
7. Exits non-zero if summary or manager feedback is empty, if literal `\\n` remains, or if manager feedback lacks `【`.

Run it with:

```powershell
ssh admin@139.224.164.156 "cd /opt/personal-development-app && PYTHONPATH=backend/src /opt/gamevoice/gamevoice-app/.venv/bin/python /tmp/pd_parallel_generation_smoke.py"
```

Expected: exit code 0. Expected model call count is 2 in the happy path, or 3-4 if one branch needs repair/regeneration.

- [ ] **Step 4: Commit and push**

Run:

```powershell
git status --short
git add backend/src/gamevoice_server/personal_development.py backend/tests/test_personal_development_minimax_robustness.py backend/tests/test_personal_development.py
git commit -m "Split coaching generation into parallel branches"
git push
```

Expected: commit pushed to GitHub main.

- [ ] **Step 5: Verify three ends**

Run:

```powershell
git status --short
git rev-parse HEAD
git rev-parse "@{u}"
Get-FileHash -Algorithm SHA256 "backend/src/gamevoice_server/personal_development.py" | Format-List
ssh admin@139.224.164.156 "sha256sum /opt/personal-development-app/backend/src/gamevoice_server/personal_development.py && systemctl is-active personal-development.service && curl -sS http://127.0.0.1:8011/health"
```

Expected:
- local working tree clean
- local `HEAD` equals upstream
- server backend file hash equals local hash
- service active
- health ok

---

## Self-Review

- Spec coverage: The plan implements the agreed two-request design: employee-visible summary/action plan and manager-only notes run as separate MiniMax calls, merged into the existing return shape, with Feishu continuing to receive only employee-visible fields.
- Risk control: Public service contract remains unchanged; mobile does not need a schema change. Formatter and validator stay in place.
- Test coverage: Tests cover branch separation, no Gallup in employee prompt, branch regeneration, escaped newline formatting through existing tests, and service-level behavior through existing personal development tests.
- Deployment: Plan includes server restart, real transcript smoke test, commit, push, and three-end verification.
