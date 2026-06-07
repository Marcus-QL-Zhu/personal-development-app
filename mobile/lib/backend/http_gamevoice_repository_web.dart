import 'gamevoice_repository.dart';

class HttpGameVoiceRepository implements GameVoiceRepository {
  HttpGameVoiceRepository({
    required this.baseUri,
    String? apiToken,
    Object? httpClient,
  }) {
    apiToken;
  }

  final Uri baseUri;

  UnsupportedError _unsupported() {
    return UnsupportedError(
      'HttpGameVoiceRepository is not available on Flutter Web yet. '
      'Use DemoGameVoiceRepository for local browser UI tests.',
    );
  }

  @override
  Future<bool> healthCheck() async => throw _unsupported();

  @override
  Future<List<TableListItem>> listTables() async => throw _unsupported();

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async =>
      throw _unsupported();

  @override
  Future<String> fetchAssistantName({required String tableId}) async =>
      throw _unsupported();

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async =>
      throw _unsupported();

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async =>
      throw _unsupported();

  @override
  Future<DocumentUploadResult> uploadFiles({
    required String tableId,
    required List<UploadFilePayload> files,
  }) async =>
      throw _unsupported();

  @override
  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  }) async =>
      throw _unsupported();

  @override
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async =>
      throw _unsupported();

  @override
  Future<CompanionReply> fetchCompanionReply({required String tableId}) async =>
      throw _unsupported();

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async =>
      throw _unsupported();

  @override
  Future<List<ContextEventRecord>> listContext(
          {required String tableId}) async =>
      throw _unsupported();

  @override
  Future<List<TtsJobRecord>> listTtsJobs({required String tableId}) async =>
      throw _unsupported();

  @override
  Future<RuntimeStateRecord> fetchRuntimeState(
          {required String tableId}) async =>
      throw _unsupported();

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses({
    required String tableId,
  }) async =>
      throw _unsupported();

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics({
    required String tableId,
  }) async =>
      throw _unsupported();

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {}

  @override
  Future<void> markTtsJobInterrupted({
    required String tableId,
    required String jobId,
  }) async =>
      throw _unsupported();

  @override
  Future<void> markTtsJobPlayed({
    required String tableId,
    required String jobId,
  }) async =>
      throw _unsupported();

  @override
  Future<TtsSegmentRecord?> fetchNextTtsSegment({
    required String tableId,
    required String jobId,
  }) async =>
      throw _unsupported();

  @override
  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async =>
      throw _unsupported();

  @override
  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async =>
      throw _unsupported();

  @override
  Future<TtsStreamRecord> startTtsStream({
    required String tableId,
    required String jobId,
  }) async =>
      throw _unsupported();

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async =>
      throw _unsupported();

  @override
  Future<void> cancelTtsStream({
    required String tableId,
    required String streamId,
  }) async =>
      throw _unsupported();

  @override
  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async =>
      throw _unsupported();

  @override
  Uri latestTtsAudioUri({required String tableId}) {
    return baseUri.replace(path: '/tables/$tableId/tts-jobs/latest/audio');
  }

  @override
  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) {
    return baseUri.replace(
      path: '/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/audio',
    );
  }

  @override
  Uri voicePreviewUri(String filename) {
    return baseUri.replace(path: '/voice-previews/$filename');
  }

  @override
  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  }) async =>
      throw _unsupported();

  @override
  Future<void> deleteTable(String tableId) async => throw _unsupported();

  @override
  Future<String> renameTable(String tableId, String name) async =>
      throw _unsupported();
}
