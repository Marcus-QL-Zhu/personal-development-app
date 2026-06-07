import 'dart:async';

import 'package:flutter/material.dart';
import '../audio/duplex_audio_session.dart';
import '../audio/voice_recorder.dart';
import '../backend/gamevoice_repository.dart';
import '../live/live_transcription_client.dart';
import '../tts/tts_audio_player.dart';

const _apiToken = String.fromEnvironment('GAMEVOICE_API_TOKEN');

class DebugFunctionsScreen extends StatefulWidget {
  final TableRecord? table;
  final GameVoiceRepository repository;
  final ValueChanged<String>? onBackendUrlChanged;

  const DebugFunctionsScreen({
    super.key,
    this.table,
    required this.repository,
    this.onBackendUrlChanged,
  });

  @override
  State<DebugFunctionsScreen> createState() => _DebugFunctionsScreenState();
}

class _DebugFunctionsScreenState extends State<DebugFunctionsScreen> {
  TableRecord? _table;
  bool _isLoadingTable = false;
  String? _tableError;

  // Debug state
  List<ContextEventRecord> _contextEvents = [];
  List<TtsJobRecord> _ttsJobs = [];
  RuntimeStateRecord? _runtimeState;
  List<RuleAnalysisRecord> _ruleAnalyses = [];
  LiveDiagnosticsRecord? _liveDiagnostics;
  String? _playbackStatus;
  bool _isRecording = false;
  bool _isBusy = false;

  // Backend URL state
  bool _backendChecked = false;
  bool _backendOnline = true;

  // Live listening state
  LiveTranscriptionClient? _liveClient;
  bool _isLiveListening = false;
  String _liveTranscript = '';
  StreamSubscription<List<int>>? _liveAudioSubscription;

  // Audio components
  late final VoiceRecorder _voiceRecorder;
  late final TtsAudioPlayer _ttsPlayer;
  late final DuplexAudioSession _duplexSession;

  String get _backendLabel {
    final uri = widget.repository.latestTtsAudioUri(tableId: _table?.id ?? '');
    return '${uri.scheme}://${uri.host}:${uri.port}';
  }

  @override
  void initState() {
    super.initState();
    _voiceRecorder = RecordVoiceRecorder();
    _ttsPlayer = NetworkTtsAudioPlayer();
    _duplexSession = PlatformDuplexAudioSession();
    _table = widget.table;
    _checkBackend();
    if (_table == null) {
      _createDebugTable();
    }
  }

  @override
  void dispose() {
    _liveAudioSubscription?.cancel();
    _liveClient?.close();
    _voiceRecorder.dispose();
    super.dispose();
  }

  Future<void> _createDebugTable() async {
    setState(() {
      _isLoadingTable = true;
      _tableError = null;
    });
    try {
      final table = await widget.repository.createTable(
        'Debug table',
        assistantName: '调试助手',
        assistantPersonality: '专业严谨，擅长解释规则',
        assistantVoiceId: null,
      );
      if (mounted) setState(() => _table = table);
    } catch (e) {
      if (mounted) setState(() => _tableError = e.toString());
    } finally {
      if (mounted) setState(() => _isLoadingTable = false);
    }
  }

  TableRecord? get _activeTable {
    if (_isLoadingTable || _tableError != null) return null;
    return _table ?? widget.table;
  }

  // ─── Refresh actions ────────────────────────────────────────────────

  Future<void> _refreshContext() async {
    final table = _activeTable;
    if (table == null) return;
    setState(() => _isBusy = true);
    try {
      final events = await widget.repository.listContext(tableId: table.id);
      if (mounted) setState(() => _contextEvents = events);
    } catch (e) {
      _showError('刷新上下文失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _refreshTtsJobs() async {
    final table = _activeTable;
    if (table == null) return;
    setState(() => _isBusy = true);
    try {
      final jobs = await widget.repository.listTtsJobs(tableId: table.id);
      if (mounted) setState(() => _ttsJobs = jobs);
    } catch (e) {
      _showError('刷新TTS列表失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _refreshRuntime() async {
    final table = _activeTable;
    if (table == null) return;
    setState(() => _isBusy = true);
    try {
      final runtime =
          await widget.repository.fetchRuntimeState(tableId: table.id);
      if (mounted) setState(() => _runtimeState = runtime);
    } catch (e) {
      _showError('刷新运行时状态失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _refreshRuleAnalyses() async {
    final table = _activeTable;
    if (table == null) return;
    setState(() => _isBusy = true);
    try {
      final analyses =
          await widget.repository.listRuleAnalyses(tableId: table.id);
      if (mounted) setState(() => _ruleAnalyses = analyses);
    } catch (e) {
      _showError('刷新规则分析失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _refreshLiveDiagnostics() async {
    final table = _activeTable;
    if (table == null) return;
    setState(() => _isBusy = true);
    try {
      final diag =
          await widget.repository.fetchLiveDiagnostics(tableId: table.id);
      if (mounted) setState(() => _liveDiagnostics = diag);
    } catch (e) {
      _showError('刷新诊断信息失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _playLatestTts() async {
    final table = _activeTable;
    if (table == null || _ttsJobs.isEmpty) return;
    setState(() => _isBusy = true);
    try {
      final latestJob = _ttsJobs.last;
      final stream = await widget.repository.startTtsStream(
        tableId: table.id,
        jobId: latestJob.jobId,
      );
      if (mounted)
        setState(() => _playbackStatus = 'Playing: ${latestJob.content}');
      await _playTtsStream(table.id, latestJob.jobId, stream.streamId);
    } catch (e) {
      _showError('播放TTS失败: $e');
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  Future<void> _playTtsStream(
      String tableId, String jobId, String streamId) async {
    while (true) {
      final chunk = await widget.repository.fetchNextTtsStreamChunk(
        tableId: tableId,
        streamId: streamId,
      );
      if (chunk == null) break;
      await widget.repository.markTtsSegmentStarted(
        tableId: tableId,
        jobId: jobId,
        segmentIndex: chunk.segmentIndex,
      );
      try {
        await _ttsPlayer.playBytes(chunk.audioBytes);
      } catch (_) {}
      await widget.repository.markTtsSegmentCompleted(
        tableId: tableId,
        jobId: jobId,
        segmentIndex: chunk.segmentIndex,
      );
      if (chunk.isFinal) break;
    }
    if (mounted) setState(() => _playbackStatus = 'Playback finished');
  }

  // ─── Recording ─────────────────────────────────────────────────────

  Future<void> _toggleRecording() async {
    if (_isRecording) {
      final clip = await _voiceRecorder.stop();
      if (mounted) setState(() => _isRecording = false);
      if (clip == null) return;
      final table = _activeTable;
      if (table == null) return;
      setState(() => _isBusy = true);
      try {
        await widget.repository.uploadVoiceClip(
          tableId: table.id,
          clip: clip,
        );
        await _refreshContext();
      } catch (e) {
        _showError('上传录音失败: $e');
      } finally {
        if (mounted) setState(() => _isBusy = false);
      }
    } else {
      final granted = await _voiceRecorder.ensurePermission();
      if (!granted) {
        _showError('麦克风权限被拒绝');
        return;
      }
      await _voiceRecorder.start();
      if (mounted) setState(() => _isRecording = true);
    }
  }

  // ─── Live listening ───────────────────────────────────────────────

  Future<void> _toggleLiveListening() async {
    if (_isLiveListening) {
      await _stopLiveListening();
    } else {
      await _startLiveListening();
    }
  }

  Future<void> _startLiveListening() async {
    final table = _activeTable;
    if (table == null) return;
    await _duplexSession.activate();
    _liveClient = WsLiveTranscriptionClient(
      backendLabel: _backendLabel,
      apiToken: _apiToken,
    );
    await _liveClient!.connect(
      tableId: table.id,
      onEvent: _handleLiveEvent,
    );
    final stream = await _voiceRecorder.startLiveStream();
    _attachLiveStream(stream);
    if (mounted) setState(() => _isLiveListening = true);
  }

  void _attachLiveStream(Stream<List<int>> stream) {
    _liveAudioSubscription?.cancel();
    _liveAudioSubscription = stream.listen((chunk) {
      _liveClient?.sendAudio(chunk);
    });
  }

  void _handleLiveEvent(LiveTranscriptEvent event) {
    if (!mounted) return;
    if (event.event == 'transcript') {
      setState(() => _liveTranscript = event.text);
    } else if (event.event == 'final') {
      setState(() => _liveTranscript = '');
      _refreshContext();
    }
  }

  Future<void> _stopLiveListening() async {
    _liveAudioSubscription?.cancel();
    _liveAudioSubscription = null;
    await _voiceRecorder.stopLiveStream();
    await _liveClient?.end();
    await _liveClient?.close();
    _liveClient = null;
    await _duplexSession.deactivate();
    if (mounted)
      setState(() {
        _isLiveListening = false;
        _liveTranscript = '';
      });
  }

  Future<void> _checkBackend() async {
    try {
      final ok = await widget.repository.healthCheck().timeout(
            const Duration(seconds: 5),
          );
      if (mounted)
        setState(() {
          _backendOnline = ok;
          _backendChecked = true;
        });
    } catch (_) {
      if (mounted)
        setState(() {
          _backendOnline = false;
          _backendChecked = true;
        });
    }
  }

  Future<void> _showUrlEditDialog() async {
    final controller = TextEditingController(text: _backendLabel);
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('修改后端地址'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            hintText: 'http://192.168.1.100:8010',
            border: OutlineInputBorder(),
          ),
          keyboardType: TextInputType.url,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
    if (result != null && result.isNotEmpty && mounted) {
      widget.onBackendUrlChanged?.call(result);
      Navigator.pop(context, 'url_changed');
    }
  }

  void _showError(String msg) {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('调试功能'),
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_isLoadingTable) {
      return const Center(
          child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 16),
          Text('正在创建调试桌...'),
        ],
      ));
    }

    if (_tableError != null) {
      return Center(child: Text('无法创建调试桌: $_tableError'));
    }

    final table = _activeTable;
    if (table == null) {
      return const Center(child: Text('没有活动的桌'));
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Table info
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('桌: ${table.name}',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 4),
                  Text('助手: ${table.assistantName}'),
                  Text('ID: ${table.id}'),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // Backend URL settings
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        _backendChecked
                            ? (_backendOnline
                                ? Icons.cloud_done
                                : Icons.cloud_off)
                            : Icons.cloud_outlined,
                        size: 20,
                        color: _backendChecked
                            ? (_backendOnline ? Colors.green : Colors.red)
                            : Colors.grey,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _backendLabel,
                          style: const TextStyle(fontSize: 13),
                        ),
                      ),
                      TextButton(
                        onPressed: _checkBackend,
                        child: const Text('检查'),
                      ),
                      TextButton(
                        onPressed: _showUrlEditDialog,
                        child: const Text('编辑'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // Recording
          _section('录音', [
            Wrap(spacing: 12, runSpacing: 12, children: [
              OutlinedButton(
                onPressed: !_isBusy ? _toggleRecording : null,
                child: Text(_isRecording ? '停止录音' : '开始录音'),
              ),
              OutlinedButton(
                onPressed: !_isBusy ? _toggleLiveListening : null,
                child: Text(_isLiveListening ? '停止聆听' : '开始实时聆听'),
              ),
            ]),
            if (_isLiveListening) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                constraints: const BoxConstraints(maxHeight: 120),
                child: SingleChildScrollView(
                  child: Text(
                    _liveTranscript.isEmpty ? '等待转写中...' : _liveTranscript,
                    style: TextStyle(
                      fontStyle: FontStyle.italic,
                      color: _liveTranscript.isEmpty
                          ? Theme.of(context).colorScheme.onSurfaceVariant
                          : null,
                    ),
                  ),
                ),
              ),
            ],
          ]),

          // Context & state
          _section('上下文与状态', [
            Wrap(spacing: 12, runSpacing: 12, children: [
              OutlinedButton(
                onPressed: !_isBusy ? _refreshContext : null,
                child: const Text('刷新上下文'),
              ),
              OutlinedButton(
                onPressed: !_isBusy ? _refreshTtsJobs : null,
                child: const Text('刷新TTS列表'),
              ),
              OutlinedButton(
                onPressed: !_isBusy ? _refreshRuntime : null,
                child: const Text('刷新运行时'),
              ),
              OutlinedButton(
                onPressed: !_isBusy ? _refreshRuleAnalyses : null,
                child: const Text('刷新规则分析'),
              ),
              OutlinedButton(
                onPressed: !_isBusy ? _refreshLiveDiagnostics : null,
                child: const Text('刷新诊断'),
              ),
            ]),
          ]),

          // Analysis & playback
          _section('分析播放', [
            Wrap(spacing: 12, runSpacing: 12, children: [
              OutlinedButton(
                onPressed:
                    !_isBusy && _ttsJobs.isNotEmpty ? _playLatestTts : null,
                child: const Text('播放最新TTS'),
              ),
            ]),
            if (_playbackStatus != null) ...[
              const SizedBox(height: 8),
              Text(_playbackStatus!),
            ],
          ]),

          // Display area
          if (_contextEvents.isNotEmpty) ...[
            const SizedBox(height: 16),
            _section('上下文事件', [
              ..._contextEvents.take(10).map((e) => Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text('[${e.kind}/${e.source}] ${e.content}',
                        style: const TextStyle(fontSize: 12)),
                  )),
            ]),
          ],

          if (_ttsJobs.isNotEmpty) ...[
            const SizedBox(height: 16),
            _section('TTS任务', [
              ..._ttsJobs.take(5).map((j) => Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text('[${j.status}] ${j.content}',
                        style: const TextStyle(fontSize: 12)),
                  )),
            ]),
          ],

          if (_runtimeState != null) ...[
            const SizedBox(height: 16),
            _section('运行时状态', [
              Text('state: ${_runtimeState!.state}',
                  style: const TextStyle(fontSize: 12)),
              Text('agentSpeaking: ${_runtimeState!.isAgentSpeaking}',
                  style: const TextStyle(fontSize: 12)),
              Text('userSpeaking: ${_runtimeState!.isUserSpeaking}',
                  style: const TextStyle(fontSize: 12)),
              Text('lastEvent: ${_runtimeState!.lastEvent}',
                  style: const TextStyle(fontSize: 12)),
            ]),
          ],

          if (_ruleAnalyses.isNotEmpty) ...[
            const SizedBox(height: 16),
            _section('规则分析', [
              ..._ruleAnalyses.take(5).map((a) => Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text('[${a.status}] ${a.query}',
                        style: const TextStyle(fontSize: 12)),
                  )),
            ]),
          ],

          if (_liveDiagnostics != null) ...[
            const SizedBox(height: 16),
            _section('实时诊断', [
              Text('ws连接: ${_liveDiagnostics!.websocketConnects}',
                  style: const TextStyle(fontSize: 12)),
              Text('音频块: ${_liveDiagnostics!.audioChunksReceived}',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'recv: dt=${_liveDiagnostics!.audioInterArrivalMs ?? '-'}ms maxDt=${_liveDiagnostics!.maxAudioInterArrivalMs ?? '-'}ms',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'burst: count=${_liveDiagnostics!.receiveBurstCount} max/s=${_liveDiagnostics!.maxReceiveBurstChunksPerSecond}',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'queue: in=${_liveDiagnostics!.audioQueueDepthOnEnqueue ?? '-'} out=${_liveDiagnostics!.audioQueueDepthOnDequeue ?? '-'} lag=${_liveDiagnostics!.sendWorkerLagMs ?? '-'}ms',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'send: audio=${_liveDiagnostics!.sendAudioElapsedMs ?? '-'}ms tencent=${_liveDiagnostics!.tencentPayloadSendElapsedMs ?? '-'}ms',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'pacing: req=${_liveDiagnostics!.sendAudioPacingRequestedMs ?? '-'}ms actual=${_liveDiagnostics!.sendAudioPacingActualMs ?? '-'}ms max=${_liveDiagnostics!.maxSendAudioPacingActualMs ?? '-'}ms',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'loop lag: last=${_liveDiagnostics!.eventLoopLagMs ?? '-'}ms max=${_liveDiagnostics!.maxEventLoopLagMs ?? '-'}ms',
                  style: const TextStyle(fontSize: 12)),
              Text(
                  'silence gate: ${_liveDiagnostics!.silenceGateState ?? '-'} pass=${_liveDiagnostics!.silenceGatePassedChunks} drop=${_liveDiagnostics!.silenceGateSuppressedChunks} dropBytes=${_liveDiagnostics!.silenceGateSuppressedBytes}',
                  style: const TextStyle(fontSize: 12)),
              Text('最终转录: ${_liveDiagnostics!.finalTranscriptsForwarded}',
                  style: const TextStyle(fontSize: 12)),
            ]),
          ],
        ],
      ),
    );
  }

  Widget _section(String title, List<Widget> children) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 8),
        ...children,
        const SizedBox(height: 16),
      ],
    );
  }
}
