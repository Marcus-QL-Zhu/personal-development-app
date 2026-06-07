import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:personal_development_app/screens/open_table_screen.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';

void main() {
  testWidgets('shows current config preview', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(
      MaterialApp(
        home: OpenTableScreen(
          repository: _MockRepository(),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('当前助手配置'), findsOneWidget);
    expect(find.text('确认开桌'), findsOneWidget);
  });
}

class _MockRepository implements GameVoiceRepository {
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
  }) async =>
      const TableRecord(
        id: 'test-table-id',
        name: 'Arkham table',
        status: 'active',
        assistantName: '宝子',
      );

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
  }) async =>
      const VoiceTranscript(
        kind: 'voice_transcript',
        filename: 'test.mp3',
        content: 'test transcript',
      );

  @override
  Future<CompanionReply> fetchCompanionReply({required String tableId}) async =>
      const CompanionReply(
        mode: 'chatty',
        transcript: 'test',
        shouldInterrupt: false,
        source: 'companion',
        content: 'test reply',
      );

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async =>
      const CompanionInterruptResult(
        interrupt: false,
        mode: 'chatty',
        source: 'companion',
        content: '',
        speechAccepted: false,
      );

  @override
  Future<List<ContextEventRecord>> listContext(
          {required String tableId}) async =>
      [];

  @override
  Future<List<TtsJobRecord>> listTtsJobs({required String tableId}) async => [];

  @override
  Future<RuntimeStateRecord> fetchRuntimeState(
          {required String tableId}) async =>
      const RuntimeStateRecord(
        state: 'idle',
        isUserSpeaking: false,
        isAgentSpeaking: false,
        lastEvent: '',
        interrupted: false,
      );

  Future<RuleAnalysisRecord> startRuleAnalysis({
    required String tableId,
    required String query,
  }) async =>
      RuleAnalysisRecord(
        analysisId: 'test',
        tableId: tableId,
        query: query,
        ackText: 'queued',
        status: 'queued',
      );

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses(
          {required String tableId}) async =>
      [];

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics(
          {required String tableId}) async =>
      const LiveDiagnosticsRecord(
        websocketConnects: 0,
        websocketDisconnects: 0,
        audioChunksReceived: 0,
        audioBytesReceived: 0,
        draftTranscriptsForwarded: 0,
        stableTranscriptsForwarded: 0,
        finalTranscriptsForwarded: 0,
        realtimeReconnects: 0,
      );

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
  }) async =>
      const TtsStreamRecord(
        streamId: 'test-stream',
        jobId: 'test-job',
        state: 'active',
        segmentCount: 0,
      );

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
  }) async =>
      const ReadResult(
        kind: 'summary',
        mode: 'summary',
        content: 'test summary',
      );

  @override
  Future<void> deleteTable(String tableId) async {}

  @override
  Future<String> renameTable(String tableId, String name) async => name;
}
