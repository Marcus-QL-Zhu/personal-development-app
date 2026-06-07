import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/app.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/live/live_transcription_client.dart';
import 'package:personal_development_app/tts/tts_audio_player.dart';

class SmokeRepository implements GameVoiceRepository {
  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async {
    return const TableRecord(
      id: 'smoke-table',
      name: 'Smoke Table',
      status: 'active',
      assistantName: '宝子',
    );
  }

  @override
  Future<String> fetchAssistantName({
    required String tableId,
  }) async {
    return '宝子';
  }

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async {
    return assistantName;
  }

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async {
    return const [];
  }

  @override
  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  }) async {
    return const ReadResult(
        kind: 'document_summary', mode: 'summary', content: 'Smoke summary');
  }

  @override
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async {
    return const VoiceTranscript(
      kind: 'voice_transcript',
      filename: 'smoke.wav',
      content: 'Smoke transcript',
    );
  }

  @override
  Future<CompanionReply> fetchCompanionReply({
    required String tableId,
  }) async {
    return const CompanionReply(
      mode: 'chatty',
      transcript: 'Smoke transcript',
      shouldInterrupt: false,
      source: 'companion',
      content: 'Smoke companion reply',
    );
  }

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async {
    return const CompanionInterruptResult(
      interrupt: false,
      mode: 'chatty',
      source: 'companion',
      content: 'Smoke auto interrupt',
      speechAccepted: false,
    );
  }

  @override
  Future<List<ContextEventRecord>> listContext({
    required String tableId,
  }) async {
    return const [];
  }

  @override
  Future<List<TtsJobRecord>> listTtsJobs({
    required String tableId,
  }) async {
    return const [];
  }

  @override
  Future<RuntimeStateRecord> fetchRuntimeState({
    required String tableId,
  }) async {
    return const RuntimeStateRecord(
      state: 'listening',
      isUserSpeaking: false,
      isAgentSpeaking: false,
      lastEvent: 'initialized',
      interrupted: false,
    );
  }

  Future<RuleAnalysisRecord> startRuleAnalysis({
    required String tableId,
    required String query,
  }) async {
    return RuleAnalysisRecord(
      analysisId: 'analysis-smoke',
      tableId: tableId,
      query: query,
      ackText: '这个我去查一下，等我一会儿。',
      status: 'queued',
      result: null,
      error: null,
    );
  }

  @override
  Future<List<TableListItem>> listTables() async {
    return const [];
  }

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses({
    required String tableId,
  }) async {
    return const [];
  }

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics({
    required String tableId,
  }) async {
    return const LiveDiagnosticsRecord(
      websocketConnects: 0,
      websocketDisconnects: 0,
      audioChunksReceived: 0,
      audioBytesReceived: 0,
      draftTranscriptsForwarded: 0,
      stableTranscriptsForwarded: 0,
      finalTranscriptsForwarded: 0,
      realtimeReconnects: 0,
    );
  }

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {}

  @override
  Future<void> markTtsJobInterrupted({
    required String tableId,
    required String jobId,
  }) async {}

  @override
  Future<void> markTtsJobPlayed({
    required String tableId,
    required String jobId,
  }) async {}

  @override
  Future<TtsSegmentRecord?> fetchNextTtsSegment({
    required String tableId,
    required String jobId,
  }) async {
    return null;
  }

  @override
  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {}

  @override
  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {}

  @override
  Future<TtsStreamRecord> startTtsStream({
    required String tableId,
    required String jobId,
  }) async {
    return const TtsStreamRecord(
      streamId: 'stream-smoke',
      jobId: 'job-smoke',
      state: 'streaming',
      segmentCount: 0,
    );
  }

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async {
    return null;
  }

  @override
  Future<void> cancelTtsStream({
    required String tableId,
    required String streamId,
  }) async {}

  @override
  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    return const <int>[];
  }

  @override
  Uri latestTtsAudioUri({
    required String tableId,
  }) {
    return Uri.parse(
        'http://10.0.2.2:8010/tables/$tableId/tts-jobs/latest/audio');
  }

  @override
  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) {
    return Uri.parse(
        'http://10.0.2.2:8010/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/audio');
  }

  @override
  Future<DocumentUploadResult> uploadFiles({
    required String tableId,
    required List<UploadFilePayload> files,
  }) async =>
      DocumentUploadResult(message: '我看到你刚刚传了 ${files.length} 个文件。');

  @override
  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  }) async {}

  @override
  Future<void> deleteTable(String tableId) async {}

  @override
  Future<String> renameTable(String tableId, String name) async => name;

  @override
  Uri voicePreviewUri(String filename) =>
      Uri.parse('http://localhost/voice-previews/$filename');
}

class SmokeLiveClient implements LiveTranscriptionClient {
  @override
  Future<void> close() async {}

  @override
  Future<void> connect({
    required String tableId,
    required LiveTranscriptCallback onEvent,
  }) async {}

  @override
  Future<void> end() async {}

  @override
  Future<void> sendAudio(List<int> chunk) async {}
}

class SmokeTtsAudioPlayer implements TtsAudioPlayer {
  @override
  String? get lastSavedPath => null;

  @override
  Stream<TtsPlaybackEvent> get events => const Stream<TtsPlaybackEvent>.empty();

  @override
  Future<void> playBytes(List<int> bytes,
      {void Function()? onCompleted}) async {}

  @override
  Future<void> stop() async {}
}

void main() {
  testWidgets('app boots into the main menu', (tester) async {
    await tester.pumpWidget(
      GameVoiceApp(
        repository: SmokeRepository(),
      ),
    );

    expect(find.text('Personal Development'), findsOneWidget);
    expect(find.text('新增顾问'), findsOneWidget);
    expect(find.text('编辑履历'), findsOneWidget);
    expect(find.text('coach历史'), findsOneWidget);
    expect(find.text('调试功能'), findsOneWidget);
  });
}
