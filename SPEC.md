# Aweson桌游助手 Public Specification

This document is the public product and architecture specification for
Aweson桌游助手. It is intentionally written as a stable open-source reference,
not as a day-by-day implementation log.

Private deployment details, API keys, account-bound voice IDs, generated audio,
uploaded user files, APKs, and local implementation plans must stay outside the
public repository.

---

## English

### 1. Product Overview

Aweson桌游助手 is a voice-first companion for tabletop gaming sessions. It
listens to live table conversation, transcribes speech, keeps table-scoped
context, answers player questions, uses tools for rules and web lookup, and
speaks back through streamed TTS.

The product goal is not to replace players. The assistant should feel like a
lightweight table companion: quick to acknowledge, able to stay quiet when the
table is busy, and capable of helping with rules, files, facts, and playful
social moments when appropriate.

### 2. Core Principles

- **Voice first:** every assistant reply should be speakable natural language.
- **Low perceived latency:** the first spoken response should arrive quickly,
  even when the full answer requires more reasoning or tool use.
- **Append-only conversation facts:** user speech, spoken assistant messages,
  unspoken interrupted assistant text, uploaded-file facts, and tool results
  are preserved as main-event facts when they matter for future context.
- **Stable table identity:** assistant name, personality, and voice selection
  are fixed when a table is opened and must not change mid-table.
- **No hidden user-text regex routing:** lookup routing is controlled by the
  assistant formal output marker, not by matching natural-language lookup words
  directly in user speech.
- **Table-scoped data:** uploaded files, speaker identities, messages, and
  diagnostics belong to the current table.
- **Open-source-safe defaults:** public code does not include real secrets,
  private server details, account-bound voice IDs, generated TTS audio, or user
  uploads.

### 3. High-Level Architecture

Aweson桌游助手 has two primary applications:

- **Backend:** Python 3.11 + FastAPI. It owns table state, realtime audio
  bridging, ASR integration, assistant orchestration, TTS generation, file
  indexing, SkillAgent tools, and persistence.
- **Mobile client:** Flutter. It owns table creation/loading, live listening,
  audio capture, TTS playback, file upload UI, and the Table Shell interface.

Typical flow:

1. Mobile opens or loads a table.
2. Mobile streams microphone audio to the backend WebSocket.
3. Backend forwards audio to realtime ASR and receives transcript events.
4. Backend commits final user speech to the table event stream.
5. Backend decides whether the assistant should respond.
6. Preview may speak first through a fast model path.
7. Formal generation produces the real continuation or lookup commitment.
8. If needed, SkillAgent runs tools and injects results back into the main
   event stream.
9. The assistant converts available context into speakable TTS.

### 4. Voice Pipeline

#### 4.1 ASR

The backend supports Tencent Cloud realtime ASR over WebSocket for live table
listening. The ASR session may provide speaker IDs, draft/stable/final
transcript slices, and speaker context.

Only final transcripts are committed as normal user messages. Draft and stable
transcripts may be used for latency-sensitive behavior such as preview
preparation, barge-in, diagnostics, or silence-gate state, but they should not
be treated as durable user facts.

#### 4.2 VAD / Silence Gate

The backend may run a local VAD-based silence gate before forwarding audio to
ASR. When the gate detects silence, it suppresses audio chunks to reduce ASR
queue buildup and latency. When speech resumes, the ASR path should start from
an empty or near-empty queue whenever possible.

The mobile Table Shell shows a compact VAD indicator:

- Green: speech is currently passing the silence gate.
- Gray: realtime listening is active but silence is currently being suppressed
  or no speech is passing.

The indicator is diagnostic only. It must not change ASR finalization,
barge-in, or upload behavior.

#### 4.3 Barge-In

When the assistant is speaking, user speech may interrupt playback. Barge-in
should avoid false triggers from tiny audio fragments. It should prefer
meaningful partial/stable speech evidence and respect short immunity windows
for certain proactive assistant speech.

If assistant speech is interrupted after some content was generated but not
spoken, the unspoken tail should be written to the main event stream and marked
as unspoken. This keeps the assistant's conversational "mental state" coherent
for future turns without pretending the user heard it.

### 5. Preview and Formal Model Routing

Preview and formal replies are intentionally different paths.

- **Preview:** route to the fastest small model that is good enough for one
  short, natural, speakable first response. Preview exists to reduce perceived
  latency and keep the table feeling alive. It should use a small context
  window and should not trigger SkillAgent.
- **Formal:** route to the main, stronger model. Formal owns the real answer,
  continuation text after preview, and internal lookup markers such as a
  trailing `<lookup>`.

Preview output must be pure spoken text. It must not include JSON, Markdown,
hidden tags, tool calls, or lookup markers.

Formal output is also spoken text, with one exception: if the user asked for
external information and the request has enough detail, formal may append
`<lookup>` at the very end. The backend must strip this marker before TTS,
before assistant-ready events, and before writing the spoken assistant message
to the main event stream.

Preview context should be short: the smaller of the latest 10 chat messages or
roughly the latest 1 minute of relevant context. Formal may see the normal
active conversation context and should account for what preview already said so
the combined spoken result still feels natural.

### 6. Assistant Prompt and Personality

The assistant prompt must receive the table's fixed assistant name,
personality, and voice selection context. These are set when the table is
opened and do not change mid-table.

The assistant should:

- speak in natural conversational Chinese by default
- avoid speaker prefixes such as `宝子：`
- avoid JSON, Markdown, or structural wrappers in user-audible replies
- match the table mood
- keep short replies short
- use uploaded files and tool results as visible context facts when available
- acknowledge lookup requests naturally before tools run

The old hard split between `chatty` and `serious` prompt modes is removed. The
main assistant uses one stable conversation prompt mode to improve context
management and model-side cache stability.

### 7. Lookup and SkillAgent

External lookup is triggered only by formal output ending in `<lookup>`.

Rules:

- Preview never triggers SkillAgent.
- User text is not regex-matched to start SkillAgent.
- Formal may append `<lookup>` only when the user requested external
  information and the request has enough information to run.
- If the request is incomplete, formal should ask a short clarification
  question and must not append `<lookup>`.
- The cleaned spoken commitment may still be played, for example:
  `我帮你查一下。<lookup>` becomes `我帮你查一下。`.

SkillAgent runs asynchronously and can use tools such as:

- Arkham rules/card orientation and lookup
- uploaded-file search and inspection
- web search through Metaso

Arkham rules/card interaction questions should prefer the Arkham-specific tool
before general web search. General news, weather, facts, and non-game web
questions may use web search.

SkillAgent final natural-language output must be ordinary speakable text. It
must not contain hidden control markers, JSON wrappers, decorative Markdown, or
non-spoken directives.

### 8. SkillAgent Scheduling

Only one SkillAgent lookup should run per table at a time. If another lookup
request arrives while one is active, the assistant should acknowledge that the
previous lookup must finish first.

When the first lookup result returns:

- The raw result is injected into the main event stream as a visible internal
  fact, for example "You just found: ...".
- The assistant then generates the user-audible spoken answer from the normal
  conversation context.
- If the first answer is fully spoken, wait a short separation window before
  speaking a queued second lookup result.
- If the first answer is interrupted, do not automatically chain into the
  second answer; let the next model turn decide what to do from context.

### 9. Uploaded Files

Uploaded files are persistent and table-scoped.

Requirements:

- Files are saved to disk, not only memory.
- Backend restart must preserve file manifests and allow historical tables to
  load their files again.
- Loading table history should show attachment count and total attachment
  size.
- Table Shell should expose an attachment menu with upload and view actions.
- The file list modal should support close, outside-click dismiss, and delete.
- Successful upload should be injected into the main event stream as a visible
  fact so the assistant and SkillAgent know the file exists.

File tools:

- Search should use bounded grep-like access rather than blindly reading entire
  files.
- PDF text extraction may use `pdfplumber==0.11.9`.
- DOCX and PDF extracted text may be cached so repeated questions do not
  repeatedly parse the same file.
- A broad inspection tool should provide a bounded document map for questions
  like "what is this file about?" or "summarize the file".

### 10. Speaker Identity

The backend maintains anonymous speaker buckets from ASR speaker IDs. Every new
identity bucket should have a safe fallback alias, such as `宝宝`, so the
assistant never has to say awkward labels like "Player A".

The identity linker may use LLM-based alias rewriting from high-value evidence
windows. It should not rely on local regex rules to directly assign names.
Names should come from actual transcript evidence and should be updated
conservatively enough to avoid mixing unrelated people, while still supporting
natural table dialogue where players call each other by name.

### 11. Proactive Assistant Speech

The assistant may speak without being explicitly called in limited cases:

- heartbeat / table warm-up after the table has been listening for several
  minutes without assistant speech
- light teasing or social reactions when table context clearly invites it
- queued lookup result return
- tool or file-related acknowledgement when it is useful

Heartbeat timing should start when live listening is active. Any assistant
speech resets the timer and schedules the next random deadline. If no reliable
player name is known, the assistant may address the group with a generic
friendly phrase instead of staying silent.

### 12. Mobile UI

The mobile app provides:

- assistant setup: name, personality template, personality description, voice
  selection
- open table
- load historical table
- Table Shell with conversation stream, assistant state, VAD indicator, file
  menu, and save/exit action
- debug tools for local testing

Public code includes only a default "server voice" placeholder. Real voice IDs
and preview audio are deployment-specific private configuration.

### 13. Persistence and Context Management

Main event stream facts include:

- final user transcripts
- spoken assistant messages
- interrupted assistant unspoken tails
- uploaded-file facts
- injected tool results
- relevant table metadata

Runtime-only diagnostics, raw TTS job details, and transient draft ASR events
should not become normal conversational facts unless explicitly transformed into
a durable event.

When conversation context grows too large, background compaction may produce a
narrative summary. Active context should be composed from the summary plus a
recent tail, while preserving the table's stable assistant identity.

### 14. Security and Open-Source Boundaries

Allowed in the public repository:

- provider names such as Tencent Cloud ASR, MiniMax, SiliconFlow, Metaso
- public API endpoint shapes
- Arkham rules/card data that the project is allowed to redistribute
- placeholder configuration names
- fake test values

Not allowed in the public repository:

- real `.env` files
- real API keys or public access tokens
- private server IPs, domains, usernames, SSH commands, or firewall rules
- account-bound voice IDs
- generated TTS audio and voice preview audio
- user uploads and runtime databases
- APKs and deployment archives
- local implementation plans and private agent notes

The public `.env.example` should contain variable names and safe defaults only.

### 15. Testing Expectations

Backend changes should have focused pytest coverage for routing, prompt-related
contracts, SkillAgent tools, file persistence, TTS streaming, and realtime
bridge behavior.

Mobile changes should have Flutter tests for repository calls, table shell
state, assistant config persistence, file UI behavior, and playback-related
state where practical.

Virtual replay tests should be used for end-to-end behavior whenever possible,
especially for preview/formal handoff, lookup flow, VAD/silence gate behavior,
barge-in, uploaded-file questions, and speaker identity linking.

---

# Aweson桌游助手 公开规格说明

本文档是 GameVoice 的公开产品与架构规格。它是稳定的开源参考，不是逐日实施流水账。

私有部署细节、API key、账号绑定 voice id、生成音频、用户上传文件、APK、本地 implementation plan 都必须留在公开仓库之外。

---

## 中文

### 1. 产品概览

Aweson桌游助手 是一个语音优先的桌游陪玩助手。它可以旁听现场桌面、实时转写语音、维护每桌独立上下文、回答玩家问题、通过工具查询规则或网页信息，并用流式 TTS 说回去。

产品目标不是替代玩家，而是像一个轻量桌面搭子：该接话时反应快，桌面忙时能安静旁听，需要时能帮忙查规则、看文件、确认事实，也能自然参与一些轻松的社交互动。

### 2. 核心原则

- **语音优先：** 助手所有可听回复都应该是自然可播报的人话。
- **低体感延迟：** 即使完整回答需要推理或工具调用，第一句也应该尽快出来。
- **主事件流追加事实：** 用户发言、助手已说内容、被打断未说内容、上传文件事实、工具结果等，只要影响后续上下文，就作为主事件事实保存。
- **桌子身份稳定：** 助手名称、人设、音色选择在开桌时确定，中途不得变化。
- **不靠用户文本正则触发查询：** 查询由 formal 输出的内部标记控制，不直接匹配用户原话里的“查一下”等自然语言。
- **数据按桌隔离：** 上传文件、说话人身份、消息、诊断都属于当前桌。
- **开源安全默认值：** 公开代码不包含真实密钥、私有服务器信息、账号绑定 voice id、生成 TTS 音频或用户上传内容。

### 3. 总体架构

Aweson桌游助手 包含两个主要应用：

- **后端：** Python 3.11 + FastAPI。负责桌子状态、实时音频桥、ASR 接入、助手编排、TTS 生成、文件索引、SkillAgent 工具和持久化。
- **移动端：** Flutter。负责开桌/加载历史桌、实时聆听、音频采集、TTS 播放、文件上传 UI 和 Table Shell 界面。

典型流程：

1. 移动端打开或加载桌子。
2. 移动端通过 WebSocket 把麦克风音频流给后端。
3. 后端把音频转发给实时 ASR，并接收转写事件。
4. 后端把 final 用户发言写入桌子主事件流。
5. 后端判断助手是否应该回应。
6. Preview 可以先通过快速模型路径说第一句。
7. Formal 生成真正的 continuation 或查询承诺。
8. 如果需要，SkillAgent 调用工具，并把结果注入回主事件流。
9. 助手基于当前上下文生成可播报 TTS。

### 4. 语音链路

#### 4.1 ASR

后端支持腾讯云实时 ASR WebSocket，用于现场桌面旁听。ASR 会返回 speaker id、draft/stable/final 转写片段以及说话人上下文。

只有 final transcript 会作为正常用户消息写入主事件流。draft 和 stable 可以用于 preview 预热、barge-in、诊断或 silence gate 状态，但不能当作持久用户事实。

#### 4.2 VAD / 静音门

后端可以在音频进入 ASR 前使用本地 VAD 静音门。检测到静音时压制音频 chunk，减少 ASR 队列堆积和延迟；再次检测到说话时，ASR 路径应尽量从空队列或接近空队列开始。

移动端 Table Shell 显示一个紧凑 VAD 指示灯：

- 绿色：当前有声音通过静音门。
- 灰色：实时聆听开启，但当前静音被拦截，或没有声音通过。

这个指示灯只用于诊断，不参与 ASR final 判定、barge-in 或文件上传控制。

#### 4.3 打断

助手说话时，用户发言可以打断播放。barge-in 应避免被极短噪音误触发，更倾向于使用有意义的 partial/stable 语音证据，并尊重某些主动发言的短免疫窗口。

如果助手内容已经生成但还没说完就被打断，未说出口的尾巴应写入主事件流并标记为未说。这样后续上下文知道助手“脑子里刚刚准备说什么”，但不会假装用户已经听到了。

### 5. Preview 与 Formal 模型分工

Preview 和 formal 回复故意走不同路径。

- **Preview：** 应接尽可能快的小模型，只要能生成一句很短、自然、可播报的第一反应即可。Preview 的职责是降低体感延迟、接住桌面氛围，不负责触发 SkillAgent。
- **Formal：** 可以接主力模型。Formal 负责真正回答、preview 之后的 continuation，以及 `<lookup>` 这类内部查询标记。

Preview 输出必须是纯可播报文本，不得包含 JSON、Markdown、隐藏标签、工具调用或查询 marker。

Formal 输出也应是可播报文本，只有一个例外：当用户要求外部信息且信息足够时，formal 可以在末尾追加 `<lookup>`。后端必须在 TTS、assistant_ready 事件和主事件流写入前剥离这个 marker。

Preview 上下文应很短：取最近 10 条聊天消息和最近约 1 分钟相关上下文中更小的一组。Formal 可以看到正常活跃上下文，并且要考虑 preview 已经说过什么，让合并后的语音结果仍然像真人接话。

### 6. 助手 Prompt 与人设

助手 prompt 必须接收当前桌固定的助手名称、人设和音色选择上下文。这些在开桌时设定，中途不变。

助手应该：

- 默认使用自然中文口语
- 避免 `宝子：` 这种说话人前缀
- 避免 JSON、Markdown 或结构化包装出现在可听回复里
- 匹配当前桌面气氛
- 短问题就短回答
- 能把上传文件和工具结果当作可见上下文事实
- 对查询请求自然承诺，再让工具异步运行

旧的 `chatty` / `serious` 硬拆分已经移除。主助手使用一个稳定的 `conversation` prompt mode，以改善上下文管理和模型侧缓存稳定性。

### 7. 查询与 SkillAgent

外部查询只由 formal 末尾 `<lookup>` 触发。

规则：

- Preview 永不触发 SkillAgent。
- 后端不对用户文本做正则匹配来启动 SkillAgent。
- 只有当用户要求外部信息且请求信息足够时，formal 才能追加 `<lookup>`。
- 如果请求信息不完整，formal 应问一句简短澄清，不得追加 `<lookup>`。
- 清洗后的查询承诺可以正常播报，例如 `我帮你查一下。<lookup>` 播报为 `我帮你查一下。`。

SkillAgent 异步运行，可以使用以下工具：

- Arkham 规则/卡牌定向查询
- 上传文件搜索和整体检查
- 通过秘塔 Metaso 做联网搜索

Arkham 规则或卡牌互动问题应优先使用 Arkham 专用工具，再考虑普通网页搜索。一般新闻、天气、事实、非游戏网页问题可以走 web search。

SkillAgent 最终自然语言输出必须是普通可播报文本，不得包含隐藏控制 marker、JSON 包装、装饰性 Markdown 或不可说的指令。

### 8. SkillAgent 调度

同一桌同一时间只允许一个 SkillAgent 查询任务运行。如果前一个查询还没完成，又来了新的查询请求，助手应说明先查完前一个。

第一个查询结果回流时：

- 原始结果作为可见内部事实注入主事件流，例如“你刚刚查询得到的结果是：……”。
- 助手再基于正常对话上下文生成用户可听的口语回答。
- 如果第一个回答完整播完，等待一个短分隔窗口后再播第二个排队查询结果。
- 如果第一个回答被打断，不自动连播第二个回答，而是让下一轮模型根据上下文自主判断。

### 9. 上传文件

上传文件必须持久化，并按桌隔离。

要求：

- 文件保存到硬盘，不能只放内存。
- 后端重启后，历史桌仍能加载文件 manifest 并读取文件。
- 加载历史桌时应显示附件数量和总大小。
- Table Shell 应提供附件菜单，包含上传和查看。
- 文件列表 modal 支持关闭、点击外部关闭和删除。
- 上传成功后，应把文件事实注入主事件流，让助手和 SkillAgent 知道当前桌有这个文件。

文件工具：

- 搜索应使用有界 grep 类访问，不应无脑整份文件塞给模型。
- PDF 文本抽取可以使用 `pdfplumber==0.11.9`。
- DOCX 和 PDF 抽取文本可以缓存，避免反复解析同一文件。
- 对“这个文件讲什么”“总结文件”这类 broad question，应提供有界文档地图，包括标题、开头、章节、短预览、行数和字符数。

### 10. 说话人身份

后端根据 ASR speaker id 维护匿名说话人桶。每个新身份桶都应有安全保底称呼，例如 `宝宝`，避免助手说出“玩家A”这类尴尬称呼。

贴名系统可以使用 LLM 从高价值证据窗口做 alias rewrite。它不应依赖本地正则直接把名字贴到 speaker bucket 上。名字必须来自真实转写证据，判断要足够谨慎，避免混淆不同人，同时也要支持自然桌面聊天中玩家互相叫名字的场景。

### 11. 助手主动开口

在有限场景下，助手可以不等用户显式点名就开口：

- 实时聆听持续数分钟且助手一直没发声时的 heartbeat
- 桌面氛围明确邀请时的轻度起哄或社交反应
- 查询结果回流
- 有必要的工具或文件相关确认

Heartbeat 计时从 live listening 开启时开始。任何助手发声都会重置计时器并生成下一次随机 deadline。如果没有可靠玩家名，助手可以用泛称呼问全员，不需要沉默等待下一轮。

### 12. 移动端 UI

移动端提供：

- 助手设定：名称、人设模板、人设描述、音色选择
- 开桌
- 加载历史桌
- Table Shell：对话流、助手状态、VAD 指示灯、文件菜单、保存退出
- 本地调试工具

公开代码只包含“服务器默认音色”占位项。真实 voice id 和试听音频属于部署私有配置。

### 13. 持久化与上下文管理

主事件流事实包括：

- final 用户转写
- 助手已说内容
- 被打断未说出口的助手尾巴
- 上传文件事实
- 工具结果注入
- 相关桌子元数据

runtime 诊断、原始 TTS job 细节、临时 draft ASR 事件不应变成普通对话事实，除非被显式转换成持久事件。

当上下文过长时，后台可以生成叙事式压缩摘要。活跃上下文由 summary 加最近尾部组成，同时保持当前桌固定助手身份。

### 14. 安全与开源边界

允许公开：

- 腾讯云 ASR、MiniMax、SiliconFlow、秘塔 Metaso 等供应商名称
- 公开 API endpoint 形态
- 项目允许再分发的 Arkham 规则/卡牌数据
- 配置变量名
- 测试假值和占位值

不允许公开：

- 真实 `.env`
- 真实 API key 或公网访问 token
- 私有服务器 IP、域名、用户名、SSH 命令、防火墙规则
- 账号绑定 voice id
- 生成 TTS 音频和 voice preview 音频
- 用户上传文件和 runtime 数据库
- APK 与部署包
- 本地 implementation plan 和私有 agent notes

公开 `.env.example` 只能包含变量名和安全默认值。

### 15. 测试预期

后端改动应有 focused pytest 覆盖：路由、prompt 合约、SkillAgent 工具、文件持久化、TTS streaming、实时 bridge 行为。

移动端改动应有 Flutter 测试覆盖：repository 调用、Table Shell 状态、助手配置持久化、文件 UI 行为，以及可测试的播放相关状态。

能用虚拟 replay 环境验证的端到端行为，应优先用虚拟环境验证，尤其是 preview/formal handoff、查询链路、VAD/silence gate、barge-in、上传文件问答和说话人贴名。
