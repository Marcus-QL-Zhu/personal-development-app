import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import '../audio/duplex_audio_session.dart';
import '../audio/voice_recorder.dart';
import '../backend/gamevoice_repository.dart';
import '../diagnostics/mobile_diagnostics_logger.dart';
import '../live/live_transcription_client.dart';
import '../tts/tts_audio_player.dart';
import '../widgets/conversation_list_view.dart';
import '../widgets/assistant_status_display.dart';

const _apiToken = String.fromEnvironment('GAMEVOICE_API_TOKEN');

class TableShellScreen extends StatefulWidget {
  const TableShellScreen({
    super.key,
    required this.table,
    required this.repository,
    this.liveClientFactory,
    this.voiceRecorderFactory,
    this.ttsPlayerFactory,
    this.duplexSessionFactory,
    this.enableRuntimePolling = true,
    this.enableStatusIdleTimer = true,
  });

  final TableRecord table;
  final GameVoiceRepository repository;
  final LiveTranscriptionClient Function(String backendLabel)?
      liveClientFactory;
  final VoiceRecorder Function()? voiceRecorderFactory;
  final TtsAudioPlayer Function()? ttsPlayerFactory;
  final DuplexAudioSession Function()? duplexSessionFactory;
  final bool enableRuntimePolling;
  final bool enableStatusIdleTimer;

  @override
  State<TableShellScreen> createState() => _TableShellScreenState();
}

class _TableShellScreenState extends State<TableShellScreen> {
  // Core components
  late final VoiceRecorder _voiceRecorder;
  late final TtsAudioPlayer _ttsPlayer;
  late final DuplexAudioSession _duplexSession;
  LiveTranscriptionClient? _liveClient;
  StreamSubscription<List<int>>? _liveAudioSubscription;
  MobileDiagnosticsLogger? _mobileDiagnosticsLogger;

  // State
  List<ContextEventRecord> _contextEvents = [];
  RuntimeStateRecord? _runtimeState;
  LiveDiagnosticsRecord? _liveDiagnostics;
  bool _isLoading = true;
  bool _isUploading = false;
  bool _isLiveListening = false;
  List<Map<String, dynamic>> _speakerIdentities = [];
  List<Map<String, dynamic>> _speakerIdentityReviewCandidates = [];

  // Live transcript display
  String _liveTranscript = '';
  String? _activeTtsJobId;
  String? _activeTtsStreamId;
  String? _activePlaybackKey;
  String? _queuedTtsJobId;
  String? _queuedTtsStreamId;
  final Set<String> _runtimeRecoveredTtsJobIds = <String>{};
  int _playbackToken = 0;
  bool _isAgentSpeaking = false;
  bool _runtimeError = false;

  // Timers
  Timer? _runtimePollingTimer;

  // Backend label from repository
  String get _backendLabel {
    final uri = widget.repository.latestTtsAudioUri(tableId: widget.table.id);
    return '${uri.scheme}://${uri.host}:${uri.port}';
  }

  @override
  void initState() {
    super.initState();
    _voiceRecorder =
        widget.voiceRecorderFactory?.call() ?? RecordVoiceRecorder();
    _ttsPlayer = widget.ttsPlayerFactory?.call() ?? NetworkTtsAudioPlayer();
    _duplexSession =
        widget.duplexSessionFactory?.call() ?? PlatformDuplexAudioSession();
    _loadContext();
    if (widget.enableRuntimePolling) {
      _startRuntimePolling();
    }
  }

  @override
  void dispose() {
    _runtimePollingTimer?.cancel();
    _liveAudioSubscription?.cancel();
    unawaited(_mobileDiagnosticsLogger?.flush());
    if (identical(MobileDiagnostics.active, _mobileDiagnosticsLogger)) {
      MobileDiagnostics.active = null;
    }
    _ttsPlayer.stop();
    _voiceRecorder.dispose();
    _liveClient?.close();
    super.dispose();
  }

  void _startRuntimePolling() {
    _runtimePollingTimer = Timer.periodic(
      const Duration(seconds: 2),
      (_) => _refreshRuntime(),
    );
  }

  Future<void> _loadContext() async {
    try {
      final events =
          await widget.repository.listContext(tableId: widget.table.id);
      if (mounted) setState(() => _contextEvents = events);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _refreshRuntime() async {
    try {
      final runtimeFuture =
          widget.repository.fetchRuntimeState(tableId: widget.table.id);
      final diagnosticsFuture = _isLiveListening
          ? widget.repository.fetchLiveDiagnostics(tableId: widget.table.id)
          : Future<LiveDiagnosticsRecord?>.value(null);
      final runtime = await runtimeFuture;
      final diagnostics = await diagnosticsFuture;
      if (mounted)
        setState(() {
          _runtimeState = runtime;
          _liveDiagnostics = diagnostics;
          _isAgentSpeaking = runtime.isAgentSpeaking || _isTtsPlaybackBusy;
        });
      if (runtime.state == 'assistant_ready' &&
          !runtime.isAgentSpeaking &&
          !_isTtsPlaybackBusy &&
          runtime.currentJobId != null &&
          runtime.currentJobId != _activeTtsJobId &&
          runtime.currentJobId != runtime.lastCompletedJobId) {
        unawaited(_recoverReadyTtsFromRuntime(runtime.currentJobId!));
      }
      if (_runtimeError && mounted) {
        _runtimeError = false;
        _showSnackBar('后端连接恢复');
      }
    } catch (_) {
      if (!_runtimeError && mounted) {
        _runtimeError = true;
        _showSnackBar('无法连接后端，请检查网络');
      }
    }
  }

  Future<void> _recoverReadyTtsFromRuntime(String jobId) async {
    if (_isTtsPlaybackBusy || _activeTtsJobId == jobId) return;
    if (!_runtimeRecoveredTtsJobIds.add(jobId)) return;
    try {
      final stream = await widget.repository.startTtsStream(
        tableId: widget.table.id,
        jobId: jobId,
      );
      if (!mounted || _isTtsPlaybackBusy || _activeTtsJobId == jobId) return;
      await _playTtsStream(jobId: jobId, streamId: stream.streamId);
    } catch (_) {
      _runtimeRecoveredTtsJobIds.remove(jobId);
    }
  }

  bool get _isThinking {
    final state = _runtimeState?.state;
    return state == 'assistant_ready' && !_isAgentSpeaking;
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
    final sessionId = 'live-${DateTime.now().toUtc().microsecondsSinceEpoch}';
    final diagnosticsLogger = MobileDiagnosticsLogger(
      tableId: widget.table.id,
      repository: widget.repository,
      sessionId: sessionId,
    );
    _mobileDiagnosticsLogger = diagnosticsLogger;
    MobileDiagnostics.active = diagnosticsLogger;
    diagnosticsLogger.record(
      component: 'table_shell',
      event: 'live_start_requested',
      details: {'table_id': widget.table.id},
    );
    final granted = await _voiceRecorder.ensurePermission();
    diagnosticsLogger.record(
      component: 'table_shell',
      event: 'permission_checked',
      details: {'granted': granted},
    );
    if (!granted) {
      unawaited(diagnosticsLogger.flush());
      _showSnackBar('麦克风权限被拒绝');
      return;
    }

    debugPrint('[LIVE][ui] start requested table=${widget.table.id}');
    await _duplexSession.activate();
    diagnosticsLogger.record(
        component: 'table_shell', event: 'duplex_activated');
    _liveClient = widget.liveClientFactory?.call(_backendLabel) ??
        WsLiveTranscriptionClient(
          backendLabel: _backendLabel,
          apiToken: _apiToken,
        );
    await _liveClient!.connect(
      tableId: widget.table.id,
      onEvent: _handleLiveTranscriptEvent,
    );
    diagnosticsLogger.record(
        component: 'table_shell', event: 'websocket_connected');
    debugPrint('[LIVE][ui] websocket connected table=${widget.table.id}');
    final stream = await _voiceRecorder.startLiveStream();
    diagnosticsLogger.record(
        component: 'table_shell', event: 'live_stream_attached');
    debugPrint(
        '[LIVE][ui] live audio stream attached table=${widget.table.id}');
    _attachLiveAudioStream(stream);
    unawaited(diagnosticsLogger.flush());
    if (mounted) setState(() => _isLiveListening = true);
  }

  void _attachLiveAudioStream(Stream<List<int>> stream) {
    _liveAudioSubscription?.cancel();
    var chunkCount = 0;
    _liveAudioSubscription = stream.listen(
      (chunk) {
        chunkCount += 1;
        if (chunkCount <= 3 || chunkCount % 20 == 0) {
          debugPrint(
              '[LIVE][ui] audio chunk #$chunkCount bytes=${chunk.length}');
          MobileDiagnostics.record(
            component: 'table_shell',
            event: 'audio_chunk_received',
            details: {'chunk': chunkCount, 'bytes': chunk.length},
          );
          unawaited(MobileDiagnostics.flush());
        }
        _liveClient?.sendAudio(chunk);
      },
      onError: (error, stackTrace) {
        debugPrint('[LIVE][ui] audio stream error: $error');
        debugPrintStack(stackTrace: stackTrace);
        MobileDiagnostics.record(
          component: 'table_shell',
          event: 'audio_stream_error',
          details: {'error': error.toString()},
        );
        unawaited(MobileDiagnostics.flush());
      },
      onDone: () {
        debugPrint('[LIVE][ui] audio stream done');
        MobileDiagnostics.record(
            component: 'table_shell', event: 'audio_stream_done');
        unawaited(MobileDiagnostics.flush());
      },
    );
  }

  Future<void> _stopLiveListening() async {
    debugPrint('[LIVE][ui] stop requested table=${widget.table.id}');
    MobileDiagnostics.record(
        component: 'table_shell', event: 'live_stop_requested');
    await _liveAudioSubscription?.cancel();
    _liveAudioSubscription = null;
    await _voiceRecorder.stopLiveStream();
    await _liveClient?.end();
    await _liveClient?.close();
    _liveClient = null;
    await _duplexSession.deactivate();
    MobileDiagnostics.record(
        component: 'table_shell', event: 'duplex_deactivated');
    await _mobileDiagnosticsLogger?.flush();
    if (identical(MobileDiagnostics.active, _mobileDiagnosticsLogger)) {
      MobileDiagnostics.active = null;
    }
    _mobileDiagnosticsLogger = null;
    if (mounted) {
      setState(() {
        _isLiveListening = false;
        _liveTranscript = '';
        _liveDiagnostics = null;
      });
    }
  }

  void _handleLiveTranscriptEvent(LiveTranscriptEvent event) {
    if (!mounted) return;
    _updateSpeakerIdentityState(event);
    if (event.event == 'transcript') {
      setState(() => _liveTranscript = event.text);
      if (_isAgentSpeaking &&
          event.sliceType == 1 &&
          _containsExplicitLocalBargeIn(event.text)) {
        _interruptActiveTts();
      }
    } else if (event.event == 'assistant_preview' ||
        event.event == 'assistant_ready') {
      if (event.event == 'assistant_ready') {
        unawaited(_loadContext());
      }
      _handleAssistantReady(event);
    } else if (event.event == 'final') {
      _loadContext();
    } else if (event.event == 'barge_in') {
      _interruptActiveTts();
    }
  }

  bool _containsExplicitLocalBargeIn(String text) {
    final normalized = _normalizeLocalBargeInText(text);
    if (normalized.isEmpty) return false;
    final assistantName =
        _normalizeLocalBargeInText(widget.table.assistantName);
    if (assistantName.isNotEmpty && normalized.contains(assistantName)) {
      return true;
    }
    const triggers = <String>{
      '\u7b49\u4e00\u4e0b',
      '\u7b49\u4e00\u7b49',
      '\u7b49\u7b49',
      '\u7b49\u4f1a',
      '\u7b49\u4f1a\u513f',
      '\u505c\u4e00\u4e0b',
      '\u505c\u4e00\u505c',
      '\u505c\u505c\u505c',
      '\u5148\u505c',
      '\u5148\u7b49\u7b49',
      '\u6253\u4f4f',
      '\u522b\u8bf4\u4e86',
      '\u5148\u522b\u8bf4',
      '\u6682\u505c',
      'wait',
      'holdon',
      'stop',
    };
    return triggers.any(normalized.contains);
  }

  String _normalizeLocalBargeInText(String text) {
    return text
        .toLowerCase()
        .replaceAll(RegExp(r'[\s,.;:!?"`~\-\(\)\[\]{}<>]+'), '');
  }

  void _updateSpeakerIdentityState(LiveTranscriptEvent event) {
    final speakerIdentities = event.speakerIdentities;
    final reviewCandidates = event.speakerIdentityReviewCandidates;
    if (speakerIdentities.isEmpty && reviewCandidates.isEmpty) {
      return;
    }
    setState(() {
      if (speakerIdentities.isNotEmpty) {
        _speakerIdentities = speakerIdentities;
      }
      if (reviewCandidates.isNotEmpty) {
        _speakerIdentityReviewCandidates = reviewCandidates;
      }
    });
  }

  Future<void> _handleAssistantReady(LiveTranscriptEvent event) async {
    final jobId = event.speechJobId;
    final streamId = event.ttsStreamId;
    if (jobId == null) return;
    if (!mounted) return;

    var actualStreamId = streamId;
    if (actualStreamId == null) {
      if (_isTtsPlaybackBusy || _activeTtsJobId == jobId) return;
      try {
        final streamRecord = await widget.repository.startTtsStream(
          tableId: widget.table.id,
          jobId: jobId,
        );
        actualStreamId = streamRecord.streamId;
      } catch (_) {
        return;
      }
    }
    await _playTtsStream(jobId: jobId, streamId: actualStreamId);
  }

  Future<void> _interruptActiveTts() async {
    final jobId = _activeTtsJobId;
    final streamId = _activeTtsStreamId;
    if (jobId == null) return;

    _playbackToken += 1;
    _queuedTtsJobId = null;
    _queuedTtsStreamId = null;
    _activePlaybackKey = null;
    await _ttsPlayer.stop();
    if (streamId != null) {
      await widget.repository.cancelTtsStream(
        tableId: widget.table.id,
        streamId: streamId,
      );
    }
    await widget.repository.markTtsJobInterrupted(
      tableId: widget.table.id,
      jobId: jobId,
    );
    await _loadContext();
    if (mounted) {
      setState(() {
        _activeTtsJobId = null;
        _activeTtsStreamId = null;
      });
    }
  }

  Future<void> _playTtsStream({
    required String jobId,
    required String streamId,
  }) async {
    final playbackKey = _ttsPlaybackKey(jobId, streamId);
    if (_activePlaybackKey == playbackKey) return;
    if (_activePlaybackKey != null) {
      _queuedTtsJobId = jobId;
      _queuedTtsStreamId = streamId;
      return;
    }
    _activePlaybackKey = playbackKey;
    _activeTtsJobId = jobId;
    _activeTtsStreamId = streamId;
    final token = ++_playbackToken;
    if (!mounted) return;

    setState(() => _isAgentSpeaking = true);

    var playedAnyChunk = false;
    var playbackFailed = false;
    while (true) {
      TtsStreamChunkRecord? chunk;
      try {
        chunk = await widget.repository.fetchNextTtsStreamChunk(
          tableId: widget.table.id,
          streamId: streamId,
        );
      } catch (_) {
        playbackFailed = true;
        break;
      }
      if (chunk == null || token != _playbackToken || !mounted) break;

      await widget.repository.markTtsSegmentStarted(
        tableId: widget.table.id,
        jobId: jobId,
        segmentIndex: chunk.segmentIndex,
      );
      MobileDiagnostics.record(
        component: 'tts',
        event: 'playback_segment_started',
        details: {
          'job_id': jobId,
          'stream_id': streamId,
          'segment_index': chunk.segmentIndex,
          'chunk_index': chunk.chunkIndex,
          'bytes': chunk.audioBytes.length,
          'text_length': chunk.text.length,
          'is_final': chunk.isFinal,
        },
      );
      unawaited(MobileDiagnostics.flush());

      try {
        await _ttsPlayer.playBytes(chunk.audioBytes);
      } catch (error) {
        playbackFailed = true;
        MobileDiagnostics.record(
          component: 'table_shell',
          event: 'tts_playback_failed',
          details: {
            'job_id': jobId,
            'stream_id': streamId,
            'segment_index': chunk.segmentIndex,
            'error': error.toString(),
          },
        );
        unawaited(MobileDiagnostics.flush());
        try {
          await widget.repository.cancelTtsStream(
            tableId: widget.table.id,
            streamId: streamId,
          );
        } catch (_) {}
        try {
          await widget.repository.markTtsJobInterrupted(
            tableId: widget.table.id,
            jobId: jobId,
          );
          await _loadContext();
        } catch (_) {}
        break;
      }
      playedAnyChunk = true;

      await widget.repository.markTtsSegmentCompleted(
        tableId: widget.table.id,
        jobId: jobId,
        segmentIndex: chunk.segmentIndex,
      );
      MobileDiagnostics.record(
        component: 'tts',
        event: 'playback_segment_completed',
        details: {
          'job_id': jobId,
          'stream_id': streamId,
          'segment_index': chunk.segmentIndex,
          'chunk_index': chunk.chunkIndex,
          'bytes': chunk.audioBytes.length,
          'text_length': chunk.text.length,
          'is_final': chunk.isFinal,
        },
      );
      unawaited(MobileDiagnostics.flush());

      if (chunk.isFinal) break;
    }

    if (mounted && token == _playbackToken) {
      if (playedAnyChunk && !playbackFailed) {
        await widget.repository.markTtsJobPlayed(
          tableId: widget.table.id,
          jobId: jobId,
        );
        MobileDiagnostics.record(
          component: 'tts',
          event: 'playback_job_played',
          details: {
            'job_id': jobId,
            'stream_id': streamId,
          },
        );
        unawaited(MobileDiagnostics.flush());
        await _loadContext();
      }
      setState(() {
        _isAgentSpeaking = false;
        _activeTtsJobId = null;
        _activeTtsStreamId = null;
        _activePlaybackKey = null;
      });
      final queuedJobId = _queuedTtsJobId;
      final queuedStreamId = _queuedTtsStreamId;
      _queuedTtsJobId = null;
      _queuedTtsStreamId = null;
      if (queuedJobId != null && queuedStreamId != null) {
        unawaited(
          _playTtsStream(jobId: queuedJobId, streamId: queuedStreamId),
        );
      }
    }
  }

  bool get _isTtsPlaybackBusy =>
      _activePlaybackKey != null ||
      _activeTtsJobId != null ||
      _activeTtsStreamId != null ||
      _queuedTtsJobId != null;

  String _ttsPlaybackKey(String jobId, String streamId) => '$jobId:$streamId';

  // ─── File upload ─────────────────────────────────────────────────

  String _speakerLabel(Map<String, dynamic> record) {
    final displayLabel = (record['display_label'] as String?)?.trim() ?? '';
    final linkedName = (record['linked_name'] as String?)?.trim() ?? '';
    if (displayLabel.isEmpty && linkedName.isEmpty) {
      return '未知说话人';
    }
    if (displayLabel.isEmpty) {
      return linkedName;
    }
    if (linkedName.isEmpty || linkedName == displayLabel) {
      return displayLabel;
    }
    return '$displayLabel（$linkedName）';
  }

  Widget _buildSpeakerIdentityPanel(BuildContext context) {
    if (_speakerIdentities.isEmpty &&
        _speakerIdentityReviewCandidates.isEmpty) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(8, 6, 8, 0),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: theme.colorScheme.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('实时说话人', style: theme.textTheme.labelLarge),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              ..._speakerIdentities.map(
                (record) => Chip(
                  avatar: const Icon(Icons.person, size: 18),
                  label: Text(_speakerLabel(record)),
                ),
              ),
              ..._speakerIdentityReviewCandidates.map(
                (record) => Chip(
                  avatar: const Icon(Icons.reviews, size: 18),
                  label: Text('待确认: ${_speakerLabel(record)}'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _pickAndUpload() async {
    final result = await _showFilePicker();
    if (result == null || result.files.isEmpty) return;

    final files = result.files
        .where((f) => f.bytes != null)
        .map((f) => UploadFilePayload(filename: f.name, bytes: f.bytes!))
        .toList();
    if (files.isEmpty) return;

    setState(() => _isUploading = true);
    try {
      final upload = await widget.repository
          .uploadFiles(tableId: widget.table.id, files: files);
      await _loadContext();
      if (mounted) _showSnackBar(upload.message);
    } catch (e) {
      if (mounted) _showSnackBar('上传失败: $e');
    } finally {
      if (mounted) setState(() => _isUploading = false);
    }
  }

  Future<FilePickerResult?> _showFilePicker() {
    return FilePicker.platform.pickFiles(allowMultiple: true, withData: true);
  }

  String _formatBytes(int bytes) {
    if (bytes < 1024 * 1024) {
      final kb = bytes / 1024;
      final value = kb == kb.roundToDouble()
          ? kb.toStringAsFixed(0)
          : kb.toStringAsFixed(1);
      return '$value KB';
    }
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  Future<void> _showFileOptionsMenu() async {
    final action = await showMenu<String>(
      context: context,
      position: const RelativeRect.fromLTRB(1000, 1000, 16, 96),
      items: const [
        PopupMenuItem(value: 'upload', child: Text('上传文件')),
        PopupMenuItem(value: 'view', child: Text('查看文件')),
      ],
    );
    if (action == 'upload') {
      await _pickAndUpload();
    } else if (action == 'view') {
      await _showDocumentLibraryModal();
    }
  }

  Future<void> _showDocumentLibraryModal() async {
    var documents = <DocumentRecord>[];
    var isLoading = true;
    String? error;

    Future<void> loadDocuments(StateSetter setModalState) async {
      setModalState(() {
        isLoading = true;
        error = null;
      });
      try {
        final loaded = await widget.repository.listDocuments(widget.table.id);
        setModalState(() => documents = loaded);
      } catch (e) {
        setModalState(() => error = e.toString());
      } finally {
        setModalState(() => isLoading = false);
      }
    }

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) {
        return StatefulBuilder(
          builder: (context, setModalState) {
            if (isLoading && documents.isEmpty && error == null) {
              unawaited(loadDocuments(setModalState));
            }
            return SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                child: SizedBox(
                  height: MediaQuery.of(context).size.height * 0.58,
                  child: Column(
                    children: [
                      Row(
                        children: [
                          Text(
                            '桌面文件',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const Spacer(),
                          IconButton(
                            tooltip: '关闭',
                            onPressed: () => Navigator.pop(context),
                            icon: const Icon(Icons.close),
                          ),
                        ],
                      ),
                      const Divider(height: 1),
                      Expanded(
                        child: isLoading
                            ? const Center(child: CircularProgressIndicator())
                            : error != null
                                ? Center(child: Text('加载失败: $error'))
                                : documents.isEmpty
                                    ? const Center(child: Text('暂无文件'))
                                    : ListView.builder(
                                        itemCount: documents.length,
                                        itemBuilder: (context, index) {
                                          final document = documents[index];
                                          return ListTile(
                                            title: Text(document.filename),
                                            subtitle: Text(
                                              '${document.status} · ${_formatBytes(document.sizeBytes)}',
                                            ),
                                            onLongPress: () async {
                                              final choice =
                                                  await showMenu<String>(
                                                context: context,
                                                position:
                                                    const RelativeRect.fromLTRB(
                                                  1000,
                                                  1000,
                                                  16,
                                                  96,
                                                ),
                                                items: const [
                                                  PopupMenuItem(
                                                    value: 'delete',
                                                    child: Text('删除'),
                                                  ),
                                                ],
                                              );
                                              if (choice == 'delete') {
                                                await _confirmDeleteDocument(
                                                  context,
                                                  setModalState,
                                                  document.filename,
                                                  loadDocuments,
                                                );
                                              }
                                            },
                                          );
                                        },
                                      ),
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }

  Future<void> _confirmDeleteDocument(
    BuildContext modalContext,
    StateSetter setModalState,
    String filename,
    Future<void> Function(StateSetter setModalState) reload,
  ) async {
    final confirm = await showDialog<bool>(
      context: modalContext,
      builder: (context) => AlertDialog(
        title: const Text('删除文件'),
        content: Text('确定删除 "$filename"？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('确认删除'),
          ),
        ],
      ),
    );
    if (confirm != true) return;
    try {
      await widget.repository.deleteDocument(
        tableId: widget.table.id,
        filename: filename,
      );
      await reload(setModalState);
    } catch (e) {
      if (mounted) _showSnackBar('删除失败: $e');
    }
  }

  void _showSnackBar(String message) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  void _onExit() {
    Navigator.pop(context);
  }

  // ─── Status helpers ─────────────────────────────────────────────

  String get _statusText {
    if (_isLiveListening) return '${widget.table.assistantName}正在听';
    if (_isUploading) return '${widget.table.assistantName}正在上传';
    return '';
  }

  bool get _isVadPassingSpeech =>
      _isLiveListening && _liveDiagnostics?.silenceGateState == 'speech';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.table.name),
        leading: TextButton(
          onPressed: _onExit,
          child: const Text('保存并退出'),
        ),
        automaticallyImplyLeading: false,
        actions: [
          if (_isLiveListening)
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: Center(
                child: Semantics(
                  label: _isVadPassingSpeech
                      ? 'VAD speech passing'
                      : 'VAD silence suppressed',
                  child: Container(
                    key: const Key('vad-status-indicator'),
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: _isVadPassingSpeech ? Colors.green : Colors.grey,
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                _buildSpeakerIdentityPanel(context),
                // Conversation list
                Expanded(
                  flex: 3,
                  child: ConversationListView(
                    events: _contextEvents,
                    assistantName: widget.table.assistantName,
                  ),
                ),
                if (_isLiveListening && _liveTranscript.isNotEmpty)
                  _buildLiveTranscriptStrip(context),
                const Divider(height: 1),
                // Bottom control bar
                SizedBox(
                  height: 80,
                  child: Row(
                    children: [
                      Expanded(
                        child: AssistantStatusDisplay(
                          assistantName: widget.table.assistantName,
                          isThinking: _isThinking,
                          isSpeaking: _isAgentSpeaking,
                          statusText: _statusText,
                          enableIdleTimer: widget.enableStatusIdleTimer,
                        ),
                      ),
                      // Live listening button
                      IconButton(
                        icon: Icon(
                          _isLiveListening
                              ? Icons.hearing
                              : Icons.hearing_disabled,
                          size: 32,
                          color: _isLiveListening ? Colors.green : null,
                        ),
                        onPressed: _toggleLiveListening,
                        tooltip: _isLiveListening ? '停止聆听' : '开始聆听',
                      ),
                      // File upload button
                      SizedBox(
                        width: 80,
                        child: _isUploading
                            ? const Center(
                                child:
                                    CircularProgressIndicator(strokeWidth: 2))
                            : IconButton(
                                icon: const Icon(Icons.attach_file, size: 32),
                                onPressed: _showFileOptionsMenu,
                                tooltip: '文件',
                              ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
    );
  }

  Widget _buildLiveTranscriptStrip(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      key: const Key('live-transcript-strip'),
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      decoration: BoxDecoration(
        color: theme.colorScheme.secondaryContainer,
        border: Border(
          top: BorderSide(color: theme.colorScheme.outlineVariant),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.graphic_eq,
            size: 18,
            color: theme.colorScheme.onSecondaryContainer,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '正在听：$_liveTranscript',
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSecondaryContainer,
                height: 1.3,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
