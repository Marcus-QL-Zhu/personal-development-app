import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/screens/load_history_screen.dart';

void main() {
  testWidgets('shows loading then empty state', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: LoadHistoryScreen(
          repository: _MockRepository(),
        ),
      ),
    );
    // Initially shows loading indicator
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}

class _MockRepository implements GameVoiceRepository {
  const _MockRepository();

  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<List<TableListItem>> listTables() async => [];

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async {
    return TableRecord(
      id: 'mock-id',
      name: name,
      status: 'idle',
      assistantName: '宝子',
    );
  }

  @override
  Future<String> fetchAssistantName({required String tableId}) async => '宝子';

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async =>
      assistantName;

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async => [];

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
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async {
    return const VoiceTranscript(
      kind: 'voice_clip',
      filename: 'mock.wav',
      content: '',
    );
  }

  @override
  Future<CompanionReply> fetchCompanionReply({required String tableId}) async {
    return const CompanionReply(
      mode: 'chat',
      transcript: '',
      shouldInterrupt: false,
      source: 'mock',
      content: '',
    );
  }

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async {
    return const CompanionInterruptResult(
      interrupt: false,
      mode: 'chat',
      source: 'mock',
      content: '',
      speechAccepted: false,
    );
  }

  @override
  Future<List<ContextEventRecord>> listContext(
          {required String tableId}) async =>
      [];

  @override
  Future<List<TtsJobRecord>> listTtsJobs({required String tableId}) async => [];

  @override
  Future<RuntimeStateRecord> fetchRuntimeState(
      {required String tableId}) async {
    return const RuntimeStateRecord(
      state: 'idle',
      isUserSpeaking: false,
      isAgentSpeaking: false,
      lastEvent: '',
      interrupted: false,
    );
  }

  Future<RuleAnalysisRecord> startRuleAnalysis({
    required String tableId,
    required String query,
  }) async {
    return RuleAnalysisRecord(
      analysisId: 'mock-id',
      tableId: 'mock-table',
      query: query,
      ackText: '',
      status: 'pending',
    );
  }

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses(
          {required String tableId}) async =>
      [];

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics(
      {required String tableId}) async {
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
  }) async =>
      null;

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
    return TtsStreamRecord(
      streamId: 'mock-stream',
      jobId: jobId,
      state: 'started',
      segmentCount: 0,
    );
  }

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async =>
      null;

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
  }) async =>
      [];

  @override
  Uri latestTtsAudioUri({required String tableId}) => Uri.parse('');

  @override
  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) =>
      Uri.parse('');

  @override
  Uri voicePreviewUri(String filename) =>
      Uri.parse('http://localhost/voice-previews/$filename');

  @override
  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  }) async {
    return const ReadResult(
      kind: 'document',
      mode: 'summary',
      content: '',
    );
  }

  @override
  Future<void> deleteTable(String tableId) async {}

  @override
  Future<String> renameTable(String tableId, String name) async => name;
}
