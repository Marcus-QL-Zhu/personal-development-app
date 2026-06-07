# SkillAgent ReAct Robustness

Date: 2026-05-19

This note amends `SPEC.md` section 7.7/7.8 behavior for SkillAgent result flow.

## Requirements

- SkillAgent prompt should ask the model to output only ordinary speakable natural language except for native tool calls. Emoji, decorative Markdown, invisible control characters, JSON, hidden tags, and non-spoken control directives are prohibited in final natural-language text.
- This prompt rule is only a behavior guide, not a safety boundary. Backend code must tolerate any valid Unicode from LLM responses, tool arguments, tool results, and web pages.
- Logging and diagnostics are best-effort side effects. A console encoding failure, trace serialization failure, or debug-print failure must never fail the user-visible SkillAgent query.
- The ReAct runner keeps a compact structured trace for each iteration: iteration number, stage, short LLM text/reasoning summaries, tool names, tool result summaries, recoverable errors, retries, and final/timeout status.
- Trace/debug data is retained inside the rule-analysis result payload when possible, but is not injected into the main event stream as spoken assistant content. The main event stream only receives the natural-language result needed by the main assistant.
- Recoverable failures retry from the last good checkpoint and each retry counts toward the existing iteration limit. Recoverable failures include transient LLM/API errors, network interruptions, malformed tool-call JSON, temporary tool failures, and logging/encoding failures.
- Deterministic business states are not retried forever. Missing user information, no matching results, or semantically invalid query arguments should become a clarification/explanation result.

## Observed Trigger

In Table 51, the SkillAgent hook triggered and `arkham_cards` returned raw card data. The run then failed before final natural-language synthesis because a debug console `print()` attempted to write Unicode characters unsupported by the Windows GBK console encoding.

The root rule is therefore: observability failures must be isolated from the user-visible lookup path.
