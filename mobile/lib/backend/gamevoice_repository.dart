import '../live/live_transcription_client.dart';

typedef PickVoiceClipCallback = Future<UploadFilePayload?> Function();

typedef RepositoryFactory = GameVoiceRepository Function(String tableId);

typedef LiveClientFactory = LiveTranscriptionClient Function(
    String backendLabel);

class TableRecord {
  const TableRecord({
    required this.id,
    required this.name,
    required this.status,
    required this.assistantName,
  });

  final String id;
  final String name;
  final String status;
  final String assistantName;
}

class TableListItem {
  const TableListItem({
    required this.id,
    required this.name,
    required this.assistantName,
    required this.status,
    required this.createdAt,
    required this.lastActiveAt,
    required this.personalityPreview,
    this.documentCount = 0,
    this.documentTotalBytes = 0,
  });

  factory TableListItem.fromJson(Map<String, dynamic> json) {
    return TableListItem(
      id: json['id'] as String,
      name: json['name'] as String,
      assistantName: json['assistant_name'] as String,
      status: json['status'] as String,
      createdAt: json['created_at'] as String? ?? '',
      lastActiveAt: json['last_active_at'] as String? ?? '',
      personalityPreview: json['personality_preview'] as String? ?? '',
      documentCount: json['document_count'] as int? ?? 0,
      documentTotalBytes: json['document_total_bytes'] as int? ?? 0,
    );
  }

  final String id;
  final String name;
  final String assistantName;
  final String status;
  final String createdAt;
  final String lastActiveAt;
  final String personalityPreview;
  final int documentCount;
  final int documentTotalBytes;
}

class DocumentRecord {
  const DocumentRecord({
    required this.filename,
    required this.status,
    this.sizeBytes = 0,
  });

  final String filename;
  final String status;
  final int sizeBytes;
}

class ReadResult {
  const ReadResult({
    required this.kind,
    required this.mode,
    required this.content,
  });

  final String kind;
  final String mode;
  final String content;
}

class VoiceTranscript {
  const VoiceTranscript({
    required this.kind,
    required this.filename,
    required this.content,
  });

  final String kind;
  final String filename;
  final String content;
}

class CompanionReply {
  const CompanionReply({
    required this.mode,
    required this.transcript,
    required this.shouldInterrupt,
    required this.source,
    required this.content,
    this.lead,
    this.tail,
    this.turnId,
    this.replyId,
  });

  final String mode;
  final String transcript;
  final bool shouldInterrupt;
  final String source;
  final String content;
  final String? lead;
  final String? tail;
  final String? turnId;
  final String? replyId;
}

class CompanionInterruptResult {
  const CompanionInterruptResult({
    required this.interrupt,
    required this.mode,
    required this.source,
    required this.content,
    required this.speechAccepted,
    this.lead,
    this.tail,
    this.turnId,
    this.replyId,
    this.speechJobId,
    this.ttsStreamId,
  });

  final bool interrupt;
  final String mode;
  final String source;
  final String content;
  final bool speechAccepted;
  final String? lead;
  final String? tail;
  final String? turnId;
  final String? replyId;
  final String? speechJobId;
  final String? ttsStreamId;
}

class ContextEventRecord {
  const ContextEventRecord({
    required this.kind,
    required this.source,
    required this.content,
  });

  final String kind;
  final String source;
  final String content;
}

class TtsJobRecord {
  const TtsJobRecord({
    required this.jobId,
    required this.content,
    required this.mode,
    required this.format,
    required this.accepted,
    required this.status,
    this.turnId,
    this.replyId,
  });

  final String jobId;
  final String content;
  final String mode;
  final String format;
  final bool accepted;
  final String status;
  final String? turnId;
  final String? replyId;
}

class TtsSegmentRecord {
  const TtsSegmentRecord({
    required this.index,
    required this.text,
    required this.status,
    required this.format,
    required this.outputPath,
  });

  final int index;
  final String text;
  final String status;
  final String format;
  final String outputPath;
}

class TtsStreamRecord {
  const TtsStreamRecord({
    required this.streamId,
    required this.jobId,
    required this.state,
    required this.segmentCount,
    this.turnId,
    this.replyId,
  });

  final String streamId;
  final String jobId;
  final String state;
  final int segmentCount;
  final String? turnId;
  final String? replyId;
}

class TtsStreamChunkRecord {
  const TtsStreamChunkRecord({
    required this.streamId,
    required this.jobId,
    required this.chunkIndex,
    required this.segmentIndex,
    required this.text,
    required this.audioBytes,
    required this.isFinal,
    this.turnId,
    this.replyId,
  });

  final String streamId;
  final String jobId;
  final int chunkIndex;
  final int segmentIndex;
  final String text;
  final List<int> audioBytes;
  final bool isFinal;
  final String? turnId;
  final String? replyId;
}

class RuntimeStateRecord {
  const RuntimeStateRecord({
    required this.state,
    required this.isUserSpeaking,
    required this.isAgentSpeaking,
    required this.lastEvent,
    required this.interrupted,
    this.currentJobId,
    this.pendingReplyText,
    this.previewReplyText,
    this.previewSourceText,
    this.lastCompletedJobId,
    this.queueDepth,
    this.currentSegmentIndex,
    this.completedSegmentCount,
  });

  final String state;
  final bool isUserSpeaking;
  final bool isAgentSpeaking;
  final String lastEvent;
  final bool interrupted;
  final String? currentJobId;
  final String? pendingReplyText;
  final String? previewReplyText;
  final String? previewSourceText;
  final String? lastCompletedJobId;
  final int? queueDepth;
  final int? currentSegmentIndex;
  final int? completedSegmentCount;
}

class RuleAnalysisRecord {
  const RuleAnalysisRecord({
    required this.analysisId,
    required this.tableId,
    required this.query,
    required this.ackText,
    required this.status,
    this.result,
    this.error,
  });

  final String analysisId;
  final String tableId;
  final String query;
  final String ackText;
  final String status;
  final CompanionReply? result;
  final String? error;
}

class LiveDiagnosticsRecord {
  const LiveDiagnosticsRecord({
    required this.websocketConnects,
    required this.websocketDisconnects,
    required this.audioChunksReceived,
    required this.audioBytesReceived,
    this.audioReceiveMonotonicMs,
    this.audioInterArrivalMs,
    this.maxAudioInterArrivalMs,
    this.receiveBurstCount = 0,
    this.maxReceiveBurstChunksPerSecond = 0,
    this.audioQueueDepthOnEnqueue,
    this.audioQueueDepthOnDequeue,
    this.sendWorkerLagMs,
    this.maxSendWorkerLagMs,
    this.sendAudioElapsedMs,
    this.maxSendAudioElapsedMs,
    this.tencentPayloadSendElapsedMs,
    this.maxTencentPayloadSendElapsedMs,
    this.sendAudioPacingRequestedMs,
    this.sendAudioPacingActualMs,
    this.maxSendAudioPacingActualMs,
    this.eventLoopLagMs,
    this.maxEventLoopLagMs,
    this.lastEventLoopLagAt,
    required this.draftTranscriptsForwarded,
    required this.stableTranscriptsForwarded,
    required this.finalTranscriptsForwarded,
    required this.realtimeReconnects,
    this.silenceGateState,
    this.silenceGatePassedChunks = 0,
    this.silenceGateSuppressedChunks = 0,
    this.silenceGateSuppressedBytes = 0,
    this.silenceGatePrerollFlushes = 0,
    this.silenceGateLastDecision,
    this.silenceGateLastError,
    this.lastAudioChunkAt,
    this.lastDraftTranscriptAt,
    this.lastStableTranscriptAt,
    this.lastFinalTranscriptAt,
    this.lastReconnectAt,
    this.lastError,
  });

  final int websocketConnects;
  final int websocketDisconnects;
  final int audioChunksReceived;
  final int audioBytesReceived;
  final double? audioReceiveMonotonicMs;
  final double? audioInterArrivalMs;
  final double? maxAudioInterArrivalMs;
  final int receiveBurstCount;
  final int maxReceiveBurstChunksPerSecond;
  final int? audioQueueDepthOnEnqueue;
  final int? audioQueueDepthOnDequeue;
  final double? sendWorkerLagMs;
  final double? maxSendWorkerLagMs;
  final double? sendAudioElapsedMs;
  final double? maxSendAudioElapsedMs;
  final double? tencentPayloadSendElapsedMs;
  final double? maxTencentPayloadSendElapsedMs;
  final double? sendAudioPacingRequestedMs;
  final double? sendAudioPacingActualMs;
  final double? maxSendAudioPacingActualMs;
  final double? eventLoopLagMs;
  final double? maxEventLoopLagMs;
  final String? lastEventLoopLagAt;
  final int draftTranscriptsForwarded;
  final int stableTranscriptsForwarded;
  final int finalTranscriptsForwarded;
  final int realtimeReconnects;
  final String? silenceGateState;
  final int silenceGatePassedChunks;
  final int silenceGateSuppressedChunks;
  final int silenceGateSuppressedBytes;
  final int silenceGatePrerollFlushes;
  final Map<String, dynamic>? silenceGateLastDecision;
  final String? silenceGateLastError;
  final String? lastAudioChunkAt;
  final String? lastDraftTranscriptAt;
  final String? lastStableTranscriptAt;
  final String? lastFinalTranscriptAt;
  final String? lastReconnectAt;
  final String? lastError;
}

class MobileDiagnosticEntry {
  const MobileDiagnosticEntry({
    required this.ts,
    required this.sessionId,
    required this.component,
    required this.event,
    this.details = const {},
  });

  final String ts;
  final String sessionId;
  final String component;
  final String event;
  final Map<String, Object?> details;

  Map<String, Object?> toJson() {
    return {
      'ts': ts,
      'session_id': sessionId,
      'component': component,
      'event': event,
      'details': details,
    };
  }
}

class UploadFilePayload {
  const UploadFilePayload({
    required this.filename,
    required this.bytes,
    this.localPath = '',
    this.recordingId = '',
    this.chunkPaths = const [],
  });

  final String filename;
  final List<int> bytes;
  final String localPath;
  final String recordingId;
  final List<String> chunkPaths;
}

class DocumentUploadResult {
  const DocumentUploadResult({
    required this.message,
    this.records = const [],
  });

  final String message;
  final List<DocumentRecord> records;
}

abstract class GameVoiceRepository {
  Future<bool> healthCheck();

  Future<List<TableListItem>> listTables();

  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  });

  Future<String> fetchAssistantName({
    required String tableId,
  });

  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  });

  Future<List<DocumentRecord>> listDocuments(String tableId);

  Future<DocumentUploadResult> uploadFiles({
    required String tableId,
    required List<UploadFilePayload> files,
  });

  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  });

  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  });

  Future<CompanionReply> fetchCompanionReply({
    required String tableId,
  });

  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  });

  Future<List<ContextEventRecord>> listContext({
    required String tableId,
  });

  Future<List<TtsJobRecord>> listTtsJobs({
    required String tableId,
  });

  Future<RuntimeStateRecord> fetchRuntimeState({
    required String tableId,
  });

  Future<List<RuleAnalysisRecord>> listRuleAnalyses({
    required String tableId,
  });

  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics({
    required String tableId,
  });

  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  });

  Future<void> markTtsJobInterrupted({
    required String tableId,
    required String jobId,
  });

  Future<void> markTtsJobPlayed({
    required String tableId,
    required String jobId,
  });

  Future<TtsSegmentRecord?> fetchNextTtsSegment({
    required String tableId,
    required String jobId,
  });

  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  });

  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  });

  Future<TtsStreamRecord> startTtsStream({
    required String tableId,
    required String jobId,
  });

  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  });

  Future<void> cancelTtsStream({
    required String tableId,
    required String streamId,
  });

  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  });

  Uri latestTtsAudioUri({
    required String tableId,
  });

  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  });

  Uri voicePreviewUri(String filename);

  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  });

  Future<void> deleteTable(String tableId);

  Future<String> renameTable(String tableId, String name);
}
