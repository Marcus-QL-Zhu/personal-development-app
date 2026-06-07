import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/audio/duplex_audio_session.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/audio/voice_recorder.dart';
import 'package:personal_development_app/live/live_transcription_client.dart';
import 'package:personal_development_app/screens/main_menu_screen.dart';
import 'package:personal_development_app/screens/assistant_setup_screen.dart';
import 'package:personal_development_app/screens/open_table_screen.dart';
import 'package:personal_development_app/screens/load_history_screen.dart';
import 'package:personal_development_app/screens/debug_functions_screen.dart';
import 'package:personal_development_app/screens/table_shell_screen.dart';
import 'package:personal_development_app/data/personality_templates.dart';
import 'package:personal_development_app/tts/tts_audio_player.dart';
import 'package:personal_development_app/widgets/conversation_list_view.dart';

/// Fake repository that returns controlled data for testing.
class FakeGameVoiceRepository implements GameVoiceRepository {
  FakeGameVoiceRepository({
    this.tableName = 'Test Table',
    this.summary = 'Test summary.',
    this.transcript = 'Test transcript.',
    this.isHealthy = true,
    this.backendLabel = 'http://10.0.2.2:8010',
    this.healthCheckHandler,
    this.listTablesHandler,
    this.listContextHandler,
    this.fetchRuntimeStateHandler,
    this.listTtsJobsHandler,
    this.listDocumentsHandler,
    this.listRuleAnalysesHandler,
    this.fetchLiveDiagnosticsHandler,
    this.startTtsStreamHandler,
    this.fetchNextTtsStreamChunkHandler,
    List<RuleAnalysisRecord>? ruleAnalyses,
    List<TtsJobRecord>? ttsJobs,
    Map<String, List<TtsStreamChunkRecord>>? streamQueues,
    RuntimeStateRecord? runtimeState,
  })  : ruleAnalyses = ruleAnalyses ?? [],
        ttsJobs = ttsJobs ?? const [],
        streamQueues = streamQueues ?? const {},
        runtimeState = runtimeState ??
            const RuntimeStateRecord(
              state: 'listening',
              isUserSpeaking: false,
              isAgentSpeaking: false,
              lastEvent: 'agent_speaking_finished',
              interrupted: false,
            );

  final String tableName;
  final String summary;
  final String transcript;
  final bool isHealthy;
  final String backendLabel;
  final Future<bool> Function()? healthCheckHandler;
  final Future<List<TableListItem>> Function()? listTablesHandler;
  final Future<List<ContextEventRecord>> Function()? listContextHandler;
  final Future<RuntimeStateRecord> Function()? fetchRuntimeStateHandler;
  final Future<List<TtsJobRecord>> Function()? listTtsJobsHandler;
  final Future<List<DocumentRecord>> Function()? listDocumentsHandler;
  final Future<List<RuleAnalysisRecord>> Function()? listRuleAnalysesHandler;
  final Future<LiveDiagnosticsRecord> Function()? fetchLiveDiagnosticsHandler;
  final Future<TtsStreamRecord> Function(String jobId)? startTtsStreamHandler;
  final Future<TtsStreamChunkRecord?> Function(String streamId)?
      fetchNextTtsStreamChunkHandler;
  List<RuleAnalysisRecord> ruleAnalyses;
  List<TtsJobRecord> ttsJobs;
  final Map<String, List<TtsStreamChunkRecord>> streamQueues;
  RuntimeStateRecord runtimeState;

  String? startedRuleAnalysisQuery;
  String? interruptedJobId;
  String? playedJobId;
  String? cancelledStreamId;
  int startTtsStreamCount = 0;
  int fetchNextTtsStreamChunkCount = 0;
  final List<String> startedSegments = [];
  final List<String> completedSegments = [];
  final List<MobileDiagnosticEntry> uploadedDiagnostics = [];
  final List<String> deletedDocuments = [];

  LiveDiagnosticsRecord liveDiagnostics = const LiveDiagnosticsRecord(
    websocketConnects: 1,
    websocketDisconnects: 0,
    audioChunksReceived: 3,
    audioBytesReceived: 24,
    draftTranscriptsForwarded: 1,
    stableTranscriptsForwarded: 1,
    finalTranscriptsForwarded: 1,
    realtimeReconnects: 0,
    lastAudioChunkAt: '2026-05-02T12:00:00Z',
    lastDraftTranscriptAt: '2026-05-02T12:00:01Z',
    lastStableTranscriptAt: '2026-05-02T12:00:02Z',
    lastFinalTranscriptAt: '2026-05-02T12:00:03Z',
    lastReconnectAt: null,
    lastError: null,
  );

  @override
  Future<bool> healthCheck() async {
    if (healthCheckHandler != null) return healthCheckHandler!();
    return isHealthy;
  }

  @override
  Future<List<TableListItem>> listTables() async {
    if (listTablesHandler != null) return listTablesHandler!();
    return const [];
  }

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async {
    return TableRecord(
      id: 'table-1',
      name: tableName,
      status: 'active',
      assistantName: assistantName ?? '宝子',
    );
  }

  @override
  Future<String> fetchAssistantName({required String tableId}) async => '宝子';

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async {
    return assistantName;
  }

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async {
    if (listDocumentsHandler != null) return listDocumentsHandler!();
    return const [
      DocumentRecord(
          filename: 'campaign-notes.txt', status: 'stored', sizeBytes: 2048),
      DocumentRecord(
          filename: 'scenario-a.txt', status: 'stored', sizeBytes: 1024),
    ];
  }

  @override
  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  }) async {
    return ReadResult(
        kind: 'document_summary', mode: 'summary', content: summary);
  }

  @override
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async {
    return VoiceTranscript(
      kind: 'voice_transcript',
      filename: clip.filename,
      content: transcript,
    );
  }

  @override
  Future<CompanionReply> fetchCompanionReply({required String tableId}) async {
    return const CompanionReply(
      mode: 'chatty',
      transcript: '先处理这个敌人吧',
      shouldInterrupt: false,
      source: 'companion',
      content: '我先记下这句：先处理这个敌人吧',
    );
  }

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt(
      {required String tableId}) async {
    return const CompanionInterruptResult(
      interrupt: true,
      mode: 'serious',
      source: 'remote',
      content: '规则答案：此时不能触发该效果。',
      speechAccepted: true,
      speechJobId: 'job-1',
      ttsStreamId: 'stream-job-1',
    );
  }

  @override
  Future<List<ContextEventRecord>> listContext(
      {required String tableId}) async {
    if (listContextHandler != null) return listContextHandler!();
    return const [
      ContextEventRecord(
          kind: 'voice_transcript', source: 'live_asr', content: '先处理这个敌人吧'),
      ContextEventRecord(
          kind: 'assistant_reply',
          source: 'companion',
          content: '规则答案：此时不能触发该效果。'),
    ];
  }

  @override
  Future<List<TtsJobRecord>> listTtsJobs({required String tableId}) async {
    if (listTtsJobsHandler != null) return listTtsJobsHandler!();
    return List<TtsJobRecord>.from(ttsJobs);
  }

  @override
  Future<RuntimeStateRecord> fetchRuntimeState(
      {required String tableId}) async {
    if (fetchRuntimeStateHandler != null) return fetchRuntimeStateHandler!();
    return runtimeState;
  }

  Future<RuleAnalysisRecord> startRuleAnalysis({
    required String tableId,
    required String query,
  }) async {
    startedRuleAnalysisQuery = query;
    final record = RuleAnalysisRecord(
      analysisId: 'analysis-${ruleAnalyses.length + 1}',
      tableId: tableId,
      query: query,
      ackText: '这个我去查一下，等我一会儿。',
      status: 'queued',
      result: null,
      error: null,
    );
    ruleAnalyses.insert(0, record);
    return record;
  }

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses(
      {required String tableId}) async {
    if (listRuleAnalysesHandler != null) return listRuleAnalysesHandler!();
    return List<RuleAnalysisRecord>.from(ruleAnalyses);
  }

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics(
      {required String tableId}) async {
    if (fetchLiveDiagnosticsHandler != null)
      return fetchLiveDiagnosticsHandler!();
    return liveDiagnostics;
  }

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {
    uploadedDiagnostics.addAll(entries);
  }

  @override
  Future<void> markTtsJobInterrupted(
      {required String tableId, required String jobId}) async {
    interruptedJobId = jobId;
  }

  @override
  Future<void> markTtsJobPlayed(
      {required String tableId, required String jobId}) async {
    playedJobId = jobId;
  }

  @override
  Future<TtsSegmentRecord?> fetchNextTtsSegment(
      {required String tableId, required String jobId}) async {
    return null;
  }

  @override
  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    startedSegments.add('$jobId:$segmentIndex');
  }

  @override
  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    completedSegments.add('$jobId:$segmentIndex');
  }

  @override
  Future<TtsStreamRecord> startTtsStream(
      {required String tableId, required String jobId}) async {
    startTtsStreamCount += 1;
    if (startTtsStreamHandler != null) return startTtsStreamHandler!(jobId);
    return TtsStreamRecord(
      streamId: 'stream-$jobId',
      jobId: jobId,
      state: 'streaming',
      segmentCount: streamQueues['stream-$jobId']?.length ?? 0,
    );
  }

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async {
    fetchNextTtsStreamChunkCount += 1;
    if (fetchNextTtsStreamChunkHandler != null) {
      return fetchNextTtsStreamChunkHandler!(streamId);
    }
    final queue = streamQueues[streamId] ?? const [];
    if (queue.isEmpty) return null;
    final next = queue.first;
    streamQueues[streamId] = queue.skip(1).toList();
    return next;
  }

  @override
  Future<void> cancelTtsStream(
      {required String tableId, required String streamId}) async {
    cancelledStreamId = streamId;
    streamQueues[streamId] = const [];
  }

  @override
  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    return <int>[1, 2, 3, segmentIndex];
  }

  @override
  Uri latestTtsAudioUri({required String tableId}) {
    return Uri.parse('$backendLabel/tables/$tableId/tts-jobs/latest/audio');
  }

  @override
  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) {
    return Uri.parse(
        '$backendLabel/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/audio');
  }

  @override
  Uri voicePreviewUri(String filename) {
    return Uri.parse('$backendLabel/static/voice-previews/$filename');
  }

  @override
  Future<DocumentUploadResult> uploadFiles(
      {required String tableId, required List<UploadFilePayload> files}) async {
    return DocumentUploadResult(
      message:
          '我看到你刚刚传了 ${files.length} 个文件：${files.map((file) => file.filename).join('、')}。要看详情的话，点开一个文件名就行。',
    );
  }

  @override
  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  }) async {
    deletedDocuments.add(filename);
  }

  @override
  Future<void> deleteTable(String tableId) async {}

  @override
  Future<String> renameTable(String tableId, String name) async => name;
}

FakeGameVoiceRepository createRepo({String tableName = 'Test Table'}) {
  return FakeGameVoiceRepository(
    tableName: tableName,
    summary: 'Test summary.',
    transcript: 'Test transcript.',
    backendLabel: 'http://10.0.2.2:8010',
    ttsJobs: const [
      TtsJobRecord(
        jobId: 'job-1',
        content: '规则答案：此时不能触发该效果。',
        mode: 'serious',
        format: 'mp3',
        accepted: true,
        status: 'ready',
      ),
    ],
  );
}

class FakeVoiceRecorder implements VoiceRecorder {
  FakeVoiceRecorder({this.permissionGranted = true})
      : _liveStreamController = StreamController<List<int>>.broadcast();

  final bool permissionGranted;
  final StreamController<List<int>> _liveStreamController;
  bool started = false;
  int liveStartCount = 0;
  int liveStopCount = 0;

  @override
  Future<bool> ensurePermission() async => permissionGranted;

  @override
  Future<void> start() async => started = true;

  @override
  Future<UploadFilePayload?> stop() async {
    started = false;
    return const UploadFilePayload(filename: 'test.wav', bytes: [1, 2, 3]);
  }

  @override
  Future<Stream<List<int>>> startLiveStream() async {
    liveStartCount += 1;
    return _liveStreamController.stream;
  }

  void emitLiveChunk(List<int> chunk) => _liveStreamController.add(chunk);

  @override
  Future<void> stopLiveStream() async => liveStopCount += 1;

  @override
  Future<void> dispose() async => _liveStreamController.close();
}

class CancelAwareVoiceRecorder implements VoiceRecorder {
  CancelAwareVoiceRecorder() {
    _liveStreamController = StreamController<List<int>>(
      onCancel: () async {
        cancelStarted = true;
        await cancelCompleter.future;
        cancelFinished = true;
      },
    );
  }

  late final StreamController<List<int>> _liveStreamController;
  final Completer<void> cancelCompleter = Completer<void>();
  bool cancelStarted = false;
  bool cancelFinished = false;
  bool stopSawFinishedCancel = false;

  @override
  Future<bool> ensurePermission() async => true;

  @override
  Future<void> start() async {}

  @override
  Future<UploadFilePayload?> stop() async => null;

  @override
  Future<Stream<List<int>>> startLiveStream() async =>
      _liveStreamController.stream;

  @override
  Future<void> stopLiveStream() async {
    stopSawFinishedCancel = cancelFinished;
  }

  @override
  Future<void> dispose() async => _liveStreamController.close();
}

class FakeLiveTranscriptionClient implements LiveTranscriptionClient {
  ValueChanged<LiveTranscriptEvent>? _onEvent;

  @override
  Future<void> connect(
      {required String tableId,
      required LiveTranscriptCallback onEvent}) async {
    _onEvent = onEvent;
  }

  @override
  Future<void> sendAudio(List<int> chunk) async {}

  @override
  Future<void> end() async {}

  @override
  Future<void> close() async {}

  void emitEvent(LiveTranscriptEvent event) => _onEvent?.call(event);
}

class FakeTtsAudioPlayer implements TtsAudioPlayer {
  final _eventController = StreamController<TtsPlaybackEvent>.broadcast();
  List<int>? lastPlayedBytes;
  final List<List<int>> playedBytes = [];
  void Function()? _onCompleted;
  bool stopped = false;
  bool autoCompletePlayback = false;

  @override
  String? lastSavedPath;

  @override
  Stream<TtsPlaybackEvent> get events => _eventController.stream;

  @override
  Future<void> playBytes(List<int> bytes,
      {void Function()? onCompleted}) async {
    lastPlayedBytes = bytes;
    playedBytes.add(bytes);
    lastSavedPath = '/mock/gamevoice_tts/latest.mp3';
    _eventController.add(const TtsPlaybackEvent(
        state: 'prepared', engine: 'file', message: 'fake chunk persisted'));
    _eventController.add(const TtsPlaybackEvent(
        state: 'playing', engine: 'fake', message: 'fake player playing'));
    _onCompleted = onCompleted;
    if (autoCompletePlayback)
      unawaited(Future<void>.microtask(completePlayback));
  }

  @override
  Future<void> stop() async {
    stopped = true;
    _onCompleted = null;
    _eventController.add(const TtsPlaybackEvent(
        state: 'stopped', engine: 'fake', message: 'fake player stopped'));
  }

  Future<void> completePlayback() async {
    final callback = _onCompleted;
    _onCompleted = null;
    _eventController.add(const TtsPlaybackEvent(
        state: 'completed', engine: 'fake', message: 'fake player completed'));
    if (callback != null) callback();
  }
}

class FakeDuplexAudioSession implements DuplexAudioSession {
  int activateCount = 0;
  int deactivateCount = 0;
  bool active = false;

  @override
  Future<void> activate() async {
    activateCount += 1;
    active = true;
  }

  @override
  Future<void> deactivate() async {
    deactivateCount += 1;
    active = false;
  }
}

void main() {
  group('MainMenuScreen navigation', () {
    testWidgets('shows 4 menu buttons', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      expect(find.text('设定助手'), findsOneWidget);
      expect(find.text('开桌'), findsOneWidget);
      expect(find.text('加载历史'), findsOneWidget);
      expect(find.text('调试功能'), findsOneWidget);
    });

    testWidgets('navigates to AssistantSetupScreen on 设定助手 tap',
        (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('设定助手'));
      await tester.pumpAndSettle();

      expect(find.byType(AssistantSetupScreen), findsOneWidget);
      expect(find.text('助手名称'), findsOneWidget);
    });

    testWidgets('navigates to OpenTableScreen on 开桌 tap', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('开桌'));
      await tester.pumpAndSettle();

      expect(find.byType(OpenTableScreen), findsOneWidget);
      expect(find.text('开桌'), findsOneWidget);
    });

    testWidgets('navigates to LoadHistoryScreen on 加载历史 tap', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('加载历史'));
      await tester.pumpAndSettle();

      expect(find.byType(LoadHistoryScreen), findsOneWidget);
      expect(find.text('加载历史'), findsOneWidget);
    });

    testWidgets('navigates to DebugFunctionsScreen on 调试功能 tap',
        (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('调试功能'));
      await tester.pumpAndSettle();

      expect(find.byType(DebugFunctionsScreen), findsOneWidget);
      expect(find.text('调试功能'), findsOneWidget);
    });

    testWidgets('back navigation from AssistantSetupScreen works',
        (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('设定助手'));
      await tester.pumpAndSettle();
      expect(find.byType(AssistantSetupScreen), findsOneWidget);

      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      expect(find.byType(MainMenuScreen), findsOneWidget);
    });
  });

  group('AssistantSetupScreen', () {
    testWidgets('shows setup screen with title', (tester) async {
      await tester.pumpWidget(
          MaterialApp(home: AssistantSetupScreen(repository: createRepo())));
      // Allow time for async SharedPreferences load
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pumpAndSettle();

      // Check for screen title
      expect(find.text('设定助手'), findsOneWidget);
      // Name input field label should be visible
      expect(find.text('助手名称'), findsOneWidget);
    });

    testWidgets('shows personality template options after scrolling',
        (tester) async {
      await tester.pumpWidget(
          MaterialApp(home: AssistantSetupScreen(repository: createRepo())));
      // Allow time for async SharedPreferences load
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pumpAndSettle();

      // Scroll to make personality templates visible
      await tester.drag(find.byType(ListView), const Offset(0, -300));
      await tester.pumpAndSettle();

      // RadioListTile widgets should be present for template selection
      expect(find.byType(RadioListTile<PersonalityTemplate>), findsWidgets);
    });
  });

  group('OpenTableScreen', () {
    testWidgets('shows current assistant config card', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('开桌'));
      await tester.pumpAndSettle();

      expect(find.text('当前助手配置'), findsOneWidget);
      expect(find.text('确认开桌'), findsOneWidget);
    });
  });

  group('LoadHistoryScreen', () {
    testWidgets('shows empty state when no tables', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('加载历史'));
      await tester.pumpAndSettle();

      expect(find.text('暂无历史记录'), findsOneWidget);
    });

    testWidgets('shows table list when tables exist', (tester) async {
      final repo = FakeGameVoiceRepository(
        listTablesHandler: () async => [
          TableListItem(
            id: 'table-1',
            name: 'Arkham Night',
            assistantName: '宝子',
            status: 'active',
            createdAt: '2026-05-01T10:00:00Z',
            lastActiveAt: '2026-05-01T12:00:00Z',
            personalityPreview: '温柔型',
          ),
        ],
        runtimeState: const RuntimeStateRecord(
          state: 'listening',
          isUserSpeaking: false,
          isAgentSpeaking: false,
          lastEvent: 'agent_speaking_finished',
          interrupted: false,
        ),
      );
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('加载历史'));
      await tester.pumpAndSettle();

      expect(find.text('Arkham Night'), findsOneWidget);
      expect(find.text('助手: 宝子'), findsOneWidget);
    });

    testWidgets('shows attachment stats for tables that have files',
        (tester) async {
      final repo = FakeGameVoiceRepository(
        listTablesHandler: () async => [
          TableListItem(
            id: 'table-1',
            name: 'Arkham Night',
            assistantName: '宝子',
            status: 'active',
            createdAt: '2026-05-01T10:00:00Z',
            lastActiveAt: '2026-05-01T12:00:00Z',
            personalityPreview: '温柔型',
            documentCount: 3,
            documentTotalBytes: 5 * 1024 * 1024,
          ),
        ],
      );
      await tester.pumpWidget(MaterialApp(
          home: MainMenuScreen(repository: repo, onBackendUrlChanged: (_) {})));

      await tester.tap(find.text('加载历史'));
      await tester.pumpAndSettle();

      expect(find.text('附件 3 个 · 5.0 MB'), findsOneWidget);
    });
  });

  group('DebugFunctionsScreen', () {
    testWidgets('shows loading then table after creation', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
        home: DebugFunctionsScreen(
          repository: repo,
        ),
      ));

      // Initially shows loading
      expect(find.text('正在创建调试桌...'), findsOneWidget);

      // After async table creation completes
      await tester.pumpAndSettle();

      // Table info should appear (repo returns tableName = 'Test Table')
      expect(find.textContaining('桌: Test Table'), findsOneWidget);
    });

    testWidgets('shows table name when table is provided', (tester) async {
      final repo = createRepo(tableName: 'Test Session');
      await tester.pumpWidget(MaterialApp(
        home: DebugFunctionsScreen(
          table: TableRecord(
              id: 't1',
              name: 'Test Session',
              status: 'active',
              assistantName: '宝子'),
          repository: repo,
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.textContaining('Test Session'), findsOneWidget);
    });

    testWidgets('shows recording buttons', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
        home: DebugFunctionsScreen(
          table: TableRecord(
              id: 't1', name: 'Test', status: 'active', assistantName: '宝子'),
          repository: repo,
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.text('开始录音'), findsOneWidget);
    });

    testWidgets('shows refresh buttons when table is active', (tester) async {
      final repo = createRepo();
      await tester.pumpWidget(MaterialApp(
        home: DebugFunctionsScreen(
          table: TableRecord(
              id: 't1', name: 'Test', status: 'active', assistantName: '宝子'),
          repository: repo,
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.text('刷新上下文'), findsOneWidget);
      expect(find.text('刷新TTS列表'), findsOneWidget);
      expect(find.text('刷新运行时'), findsOneWidget);
    });
  });

  group('Tabletop conversation log', () {
    testWidgets(
        'renders context events as a left aligned tabletop record stream',
        (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 420,
              height: 320,
              child: ConversationListView(
                assistantName: '宝子',
                events: [
                  ContextEventRecord(
                    kind: 'voice_transcript',
                    source: 'live_asr',
                    content: '玩家A：我先调查这里。',
                  ),
                  ContextEventRecord(
                    kind: 'assistant_spoken',
                    source: 'companion',
                    content: '宝子：这个检定要先看难度。',
                  ),
                  ContextEventRecord(
                    kind: 'assistant_unspoken',
                    source: 'companion',
                    content: '宝子（未说）：后半句还没播完。',
                  ),
                ],
              ),
            ),
          ),
        ),
      );

      expect(find.byKey(const Key('tabletop-log-list')), findsOneWidget);
      expect(find.text('玩家'), findsOneWidget);
      expect(find.text('宝子'), findsNWidgets(2));
      expect(find.text('未说完'), findsOneWidget);

      final firstTop = tester.getTopLeft(find.text('玩家A：我先调查这里。')).dy;
      final secondTop = tester.getTopLeft(find.text('宝子：这个检定要先看难度。')).dy;
      final thirdTop = tester.getTopLeft(find.text('宝子（未说）：后半句还没播完。')).dy;
      expect(firstTop, lessThan(secondTop));
      expect(secondTop, lessThan(thirdTop));
    });

    testWidgets('renders injected rule references as visible unspoken results',
        (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConversationListView(
              assistantName: 'Baozi',
              events: [
                ContextEventRecord(
                  kind: 'rule_reference',
                  source: 'rule_analysis',
                  content:
                      '\u4f60\u521a\u521a\u67e5\u8be2\u5f97\u5230\u7684\u7ed3\u679c\u662f\uff1aTony Morgan rewards bounties.',
                ),
              ],
            ),
          ),
        ),
      );

      expect(
        find.text('\u67e5\u8be2\u7ed3\u679c'),
        findsOneWidget,
      );
      expect(
        find.text('\u672a\u64ad\u62a5'),
        findsOneWidget,
      );
      expect(
        find.text(
            '\u4f60\u521a\u521a\u67e5\u8be2\u5f97\u5230\u7684\u7ed3\u679c\u662f\uff1aTony Morgan rewards bounties.'),
        findsOneWidget,
      );
    });

    testWidgets('renders repeated context event kinds without dropping content',
        (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ConversationListView(
              assistantName: 'Baozi',
              events: [
                ContextEventRecord(
                  kind: 'assistant_spoken',
                  source: 'companion',
                  content: 'first assistant line',
                ),
                ContextEventRecord(
                  kind: 'assistant_spoken',
                  source: 'companion',
                  content: 'second assistant line',
                ),
                ContextEventRecord(
                  kind: 'voice_transcript',
                  source: 'live_asr',
                  content: 'first player line',
                ),
                ContextEventRecord(
                  kind: 'voice_transcript',
                  source: 'live_asr',
                  content: 'second player line',
                ),
              ],
            ),
          ),
        ),
      );

      expect(find.text('first assistant line'), findsOneWidget);
      expect(find.text('second assistant line'), findsOneWidget);
      expect(find.text('first player line'), findsOneWidget);
      expect(find.text('second player line'), findsOneWidget);
    });

    testWidgets(
        'shows live transcript in bottom strip without appending it to context log',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        listContextHandler: () async => const [
          ContextEventRecord(
            kind: 'assistant_spoken',
            source: 'companion',
            content: '宝子：主事件流里已有的一句话。',
          ),
        ],
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: '测试桌',
              status: 'active',
              assistantName: '宝子',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'transcript',
          sliceType: 1,
          text: '玩家A：这是实时字幕，不进主流。',
        ),
      );
      await tester.pump();

      expect(find.byKey(const Key('live-transcript-strip')), findsOneWidget);
      expect(find.textContaining('玩家A：这是实时字幕，不进主流。'), findsOneWidget);
      expect(
          find.byKey(const Key('context-event-live-transcript')), findsNothing);
    });

    testWidgets(
        'waits for live audio subscription cancellation before stopping recorder',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = CancelAwareVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository();

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Live Restart Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      await tester.tap(find.byIcon(Icons.hearing));
      await tester.pump();

      expect(fakeRecorder.cancelStarted, isTrue);
      expect(fakeRecorder.stopSawFinishedCancel, isFalse);

      fakeRecorder.cancelCompleter.complete();
      await tester.pump();
      await tester.pump();

      expect(fakeRecorder.stopSawFinishedCancel, isTrue);
    });

    testWidgets('shows green VAD indicator when backend is passing speech',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        fetchLiveDiagnosticsHandler: () async => const LiveDiagnosticsRecord(
          websocketConnects: 1,
          websocketDisconnects: 0,
          audioChunksReceived: 1,
          audioBytesReceived: 6400,
          draftTranscriptsForwarded: 0,
          stableTranscriptsForwarded: 0,
          finalTranscriptsForwarded: 0,
          realtimeReconnects: 0,
          silenceGateState: 'speech',
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'VAD Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.byKey(const Key('vad-status-indicator')), findsNothing);

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      await tester.pump(const Duration(seconds: 2));

      final indicator = tester.widget<Container>(
        find.byKey(const Key('vad-status-indicator')),
      );
      final decoration = indicator.decoration! as BoxDecoration;
      expect(decoration.color, Colors.green);
    });

    testWidgets('shows gray VAD indicator while silence is suppressed',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        fetchLiveDiagnosticsHandler: () async => const LiveDiagnosticsRecord(
          websocketConnects: 1,
          websocketDisconnects: 0,
          audioChunksReceived: 1,
          audioBytesReceived: 6400,
          draftTranscriptsForwarded: 0,
          stableTranscriptsForwarded: 0,
          finalTranscriptsForwarded: 0,
          realtimeReconnects: 0,
          silenceGateState: 'idle',
          silenceGateSuppressedChunks: 1,
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'VAD Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      await tester.pump(const Duration(seconds: 2));

      final indicator = tester.widget<Container>(
        find.byKey(const Key('vad-status-indicator')),
      );
      final decoration = indicator.decoration! as BoxDecoration;
      expect(decoration.color, Colors.grey);
    });

    testWidgets('opens file menu and shows document modal with delete action',
        (tester) async {
      final repo = FakeGameVoiceRepository(
        listDocumentsHandler: () async => const [
          DocumentRecord(
            filename: 'rules.txt',
            status: 'stored',
            sizeBytes: 1536,
          ),
        ],
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Files Table',
              status: 'active',
              assistantName: '宝子',
            ),
            repository: repo,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byIcon(Icons.attach_file));
      await tester.pumpAndSettle();

      expect(find.text('上传文件'), findsOneWidget);
      expect(find.text('查看文件'), findsOneWidget);

      await tester.tap(find.text('查看文件'));
      await tester.pumpAndSettle();

      expect(find.text('桌面文件'), findsOneWidget);
      expect(find.text('rules.txt'), findsOneWidget);
      expect(find.textContaining('1.5 KB'), findsOneWidget);

      await tester.longPress(find.text('rules.txt'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('确认删除'));
      await tester.pumpAndSettle();

      expect(repo.deletedDocuments, ['rules.txt']);
    });
  });

  group('TableShellScreen live playback', () {
    testWidgets(
        'refreshes context when assistant ready may include lookup reference',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      var contextCalls = 0;
      final repo = FakeGameVoiceRepository(
        listContextHandler: () async {
          contextCalls += 1;
          if (contextCalls == 1) {
            return const [];
          }
          return const [
            ContextEventRecord(
              kind: 'rule_reference',
              source: 'rule_analysis',
              content:
                  '\u4f60\u521a\u521a\u67e5\u8be2\u5f97\u5230\u7684\u7ed3\u679c\u662f\uff1aTrump news summary.',
            ),
          ];
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Lookup Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_ready',
          content: 'Lookup answer is ready.',
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(
        find.text(
            '\u4f60\u521a\u521a\u67e5\u8be2\u5f97\u5230\u7684\u7ed3\u679c\u662f\uff1aTrump news summary.'),
        findsOneWidget,
      );
    });

    testWidgets('plays assistant preview stream from live websocket events',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      var contextCalls = 0;
      final repo = FakeGameVoiceRepository(
        listContextHandler: () async {
          contextCalls += 1;
          if (contextCalls == 1) {
            return const [];
          }
          return const [
            ContextEventRecord(
              kind: 'assistant_spoken',
              source: 'companion',
              content: 'assistant spoken after playback',
            ),
          ];
        },
        streamQueues: {
          'stream-preview-1': [
            const TtsStreamChunkRecord(
              streamId: 'stream-preview-1',
              jobId: 'job-preview-1',
              chunkIndex: 0,
              segmentIndex: 0,
              text: '我来啦。',
              audioBytes: [1, 2, 3, 4],
              isFinal: true,
            ),
          ],
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: '测试桌',
              status: 'active',
              assistantName: '宝子',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byTooltip('开始聆听'));
      await tester.pump();

      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_preview',
          speechJobId: 'job-preview-1',
          ttsStreamId: 'stream-preview-1',
          content: '我来啦。',
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 10));

      expect(fakePlayer.playedBytes, [
        [1, 2, 3, 4],
      ]);
      expect(repo.playedJobId, 'job-preview-1');
      expect(find.text('assistant spoken after playback'), findsOneWidget);
    });

    testWidgets('does not interrupt local playback for ordinary partial text',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final firstFetch = Completer<TtsStreamChunkRecord?>();
      final repo = FakeGameVoiceRepository(
        streamQueues: {'stream-live-1': []},
        fetchNextTtsStreamChunkHandler: (streamId) async {
          expect(streamId, 'stream-live-1');
          return firstFetch.future;
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Barge In Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_ready',
          speechJobId: 'job-live-1',
          ttsStreamId: 'stream-live-1',
          content: 'live playback',
        ),
      );
      await tester.pump();

      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'transcript',
          sliceType: 1,
          text: 'I think this is fine',
        ),
      );
      await tester.pump();

      expect(fakePlayer.stopped, isFalse);
      expect(repo.interruptedJobId, isNull);

      firstFetch.complete(null);
    });

    testWidgets('interrupts local playback for explicit partial barge in text',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final firstFetch = Completer<TtsStreamChunkRecord?>();
      final repo = FakeGameVoiceRepository(
        streamQueues: {'stream-live-1': []},
        fetchNextTtsStreamChunkHandler: (streamId) async {
          expect(streamId, 'stream-live-1');
          return firstFetch.future;
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Barge In Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byIcon(Icons.hearing_disabled));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_ready',
          speechJobId: 'job-live-1',
          ttsStreamId: 'stream-live-1',
          content: 'live playback',
        ),
      );
      await tester.pump();

      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'transcript',
          sliceType: 1,
          text: 'Baozi wait',
        ),
      );
      await tester.pump();

      expect(fakePlayer.stopped, isTrue);
      expect(repo.interruptedJobId, 'job-live-1');

      firstFetch.complete(null);
    });

    testWidgets('records mobile diagnostics for TTS playback timing',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        streamQueues: {
          'stream-preview-1': [
            const TtsStreamChunkRecord(
              streamId: 'stream-preview-1',
              jobId: 'job-preview-1',
              chunkIndex: 0,
              segmentIndex: 0,
              text: '我来啦。',
              audioBytes: [1, 2, 3, 4],
              isFinal: true,
            ),
          ],
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: '测试桌',
              status: 'active',
              assistantName: '宝子',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byTooltip('开始聆听'));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_preview',
          speechJobId: 'job-preview-1',
          ttsStreamId: 'stream-preview-1',
          content: '我来啦。',
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));

      final playbackEvents =
          repo.uploadedDiagnostics.where((entry) => entry.component == 'tts');
      expect(
        playbackEvents.map((entry) => entry.event),
        containsAll(<String>[
          'playback_segment_started',
          'playback_segment_completed',
          'playback_job_played',
        ]),
      );
      expect(
        playbackEvents
            .firstWhere((entry) => entry.event == 'playback_segment_started')
            .details['job_id'],
        'job-preview-1',
      );
    });

    testWidgets('recovers ready assistant playback from runtime polling',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        runtimeState: const RuntimeStateRecord(
          state: 'assistant_ready',
          isUserSpeaking: false,
          isAgentSpeaking: false,
          lastEvent: 'agent_reply_ready',
          interrupted: false,
          currentJobId: 'job-ready-1',
        ),
        streamQueues: {
          'stream-job-ready-1': [
            const TtsStreamChunkRecord(
              streamId: 'stream-job-ready-1',
              jobId: 'job-ready-1',
              chunkIndex: 0,
              segmentIndex: 0,
              text: 'runtime recovery playback',
              audioBytes: [5, 6, 7],
              isFinal: true,
            ),
          ],
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Runtime Recovery Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.pump(const Duration(milliseconds: 2100));
      await tester.pump(const Duration(milliseconds: 20));

      expect(fakePlayer.playedBytes, [
        [5, 6, 7],
      ]);
      expect(repo.playedJobId, 'job-ready-1');
    });

    testWidgets(
        'does not recover runtime playback while websocket stream is active',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final firstFetch = Completer<TtsStreamChunkRecord?>();
      final repo = FakeGameVoiceRepository(
        runtimeState: const RuntimeStateRecord(
          state: 'assistant_ready',
          isUserSpeaking: false,
          isAgentSpeaking: false,
          lastEvent: 'agent_reply_ready',
          interrupted: false,
          currentJobId: 'job-live-1',
        ),
        fetchNextTtsStreamChunkHandler: (streamId) async {
          expect(streamId, 'stream-live-1');
          return firstFetch.future;
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Runtime Race Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byTooltip('开始聆听'));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_ready',
          speechJobId: 'job-live-1',
          ttsStreamId: 'stream-live-1',
          content: 'live playback',
        ),
      );
      await tester.pump();

      expect(repo.fetchNextTtsStreamChunkCount, 1);
      await tester.pump(const Duration(milliseconds: 2100));
      await tester.pump();

      expect(repo.startTtsStreamCount, 0);
      expect(repo.fetchNextTtsStreamChunkCount, 1);

      firstFetch.complete(
        const TtsStreamChunkRecord(
          streamId: 'stream-live-1',
          jobId: 'job-live-1',
          chunkIndex: 0,
          segmentIndex: 0,
          text: 'live playback',
          audioBytes: [9, 9, 9],
          isFinal: true,
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));

      expect(fakePlayer.playedBytes, [
        [9, 9, 9],
      ]);
      expect(repo.playedJobId, 'job-live-1');
    });

    testWidgets('does not mark a TTS job played when stream fetch fails',
        (tester) async {
      final fakeClient = FakeLiveTranscriptionClient();
      final fakeRecorder = FakeVoiceRecorder();
      final fakePlayer = FakeTtsAudioPlayer();
      final fakeDuplex = FakeDuplexAudioSession();
      final repo = FakeGameVoiceRepository(
        fetchNextTtsStreamChunkHandler: (_) async {
          throw const HttpException('Request failed: 404 stream not found');
        },
      );

      await tester.pumpWidget(
        MaterialApp(
          home: TableShellScreen(
            table: const TableRecord(
              id: 'table-1',
              name: 'Missing Stream Table',
              status: 'active',
              assistantName: 'Baozi',
            ),
            repository: repo,
            liveClientFactory: (_) => fakeClient,
            voiceRecorderFactory: () => fakeRecorder,
            ttsPlayerFactory: () => fakePlayer,
            duplexSessionFactory: () => fakeDuplex,
            enableRuntimePolling: false,
            enableStatusIdleTimer: false,
          ),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      await tester.tap(find.byTooltip('开始聆听'));
      await tester.pump();
      fakeClient.emitEvent(
        const LiveTranscriptEvent(
          event: 'assistant_ready',
          speechJobId: 'job-missing-stream',
          ttsStreamId: 'stream-missing',
          content: 'missing stream',
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));

      expect(fakePlayer.playedBytes, isEmpty);
      expect(repo.playedJobId, isNull);
      expect(repo.startedSegments, isEmpty);
      expect(repo.completedSegments, isEmpty);
    });
  });
}
