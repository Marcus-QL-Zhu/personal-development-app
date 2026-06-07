import 'dart:collection';

import 'gamevoice_repository.dart';

class DemoGameVoiceRepository implements GameVoiceRepository {
  DemoGameVoiceRepository({Uri? baseUri})
      : baseUri = baseUri ?? Uri.parse('http://localhost:8010') {
    _seed();
  }

  final Uri baseUri;
  final Map<String, _DemoTable> _tables = LinkedHashMap<String, _DemoTable>();
  final Map<String, int> _streamOffsets = <String, int>{};
  int _nextTableId = 1;

  void _seed() {
    if (_tables.isNotEmpty) {
      return;
    }
    final table = _DemoTable(
      id: 'demo-table-1',
      name: 'Local Browser Table',
      assistantName: 'Baozi',
      createdAt: DateTime.now().toIso8601String(),
      documents: _demoDocuments(),
      context: <ContextEventRecord>[
        const ContextEventRecord(
          kind: 'system',
          source: 'demo',
          content: 'Demo table is ready for browser UI testing.',
        ),
        const ContextEventRecord(
          kind: 'player_spoken',
          source: 'player',
          content: 'Can I trigger this ability before drawing the mythos card?',
        ),
        const ContextEventRecord(
          kind: 'assistant_spoken',
          source: 'companion',
          content:
              'Demo answer: resolve the current effect first, then continue.',
        ),
      ],
    );
    _tables[table.id] = table;
    _nextTableId = 2;
  }

  _DemoTable _table(String tableId) {
    final table = _tables[tableId];
    if (table == null) {
      throw StateError('Unknown demo table: $tableId');
    }
    return table;
  }

  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<List<TableListItem>> listTables() async {
    return _tables.values.map((table) => table.toListItem()).toList();
  }

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async {
    final id = 'demo-table-${_nextTableId++}';
    final table = _DemoTable(
      id: id,
      name: name,
      assistantName: assistantName?.trim().isNotEmpty == true
          ? assistantName!.trim()
          : 'Baozi',
      createdAt: DateTime.now().toIso8601String(),
      documents: _demoDocuments(),
      context: <ContextEventRecord>[
        ContextEventRecord(
          kind: 'system',
          source: 'demo',
          content: 'Opened local browser demo table "$name".',
        ),
        const ContextEventRecord(
          kind: 'assistant_spoken',
          source: 'companion',
          content:
              'Demo mode is active. You can test navigation and state flows.',
        ),
      ],
    );
    _tables[id] = table;
    return table.toRecord();
  }

  @override
  Future<String> fetchAssistantName({required String tableId}) async {
    return _table(tableId).assistantName;
  }

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async {
    _table(tableId).assistantName = assistantName;
    return assistantName;
  }

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async {
    return List<DocumentRecord>.unmodifiable(_table(tableId).documents);
  }

  @override
  Future<DocumentUploadResult> uploadFiles({
    required String tableId,
    required List<UploadFilePayload> files,
  }) async {
    final table = _table(tableId);
    final uploadedNames = <String>[];
    for (final file in files) {
      final filename = _uniqueDocumentName(
        file.filename,
        table.documents.map((document) => document.filename).toSet(),
      );
      uploadedNames.add(filename);
      table.documents.add(
        DocumentRecord(
          filename: filename,
          status: 'stored',
          sizeBytes: file.bytes.length,
        ),
      );
      table.context.add(
        ContextEventRecord(
          kind: 'document_uploaded',
          source: 'demo',
          content: 'Uploaded $filename (${file.bytes.length} bytes).',
        ),
      );
    }
    return DocumentUploadResult(
      message: '我看到你刚刚传了 ${uploadedNames.length} 个文件：${uploadedNames.join('、')}。要看详情的话，点开一个文件名就行。',
      records: List<DocumentRecord>.unmodifiable(table.documents),
    );
  }

  @override
  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  }) async {
    final table = _table(tableId);
    table.documents.removeWhere((document) => document.filename == filename);
  }

  @override
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async {
    final table = _table(tableId);
    const content = 'Demo transcript from browser recorder.';
    table.context.add(
      const ContextEventRecord(
        kind: 'voice_transcript',
        source: 'player',
        content: content,
      ),
    );
    return const VoiceTranscript(
      kind: 'voice_transcript',
      filename: 'browser-demo.wav',
      content: content,
    );
  }

  @override
  Future<CompanionReply> fetchCompanionReply({required String tableId}) async {
    final table = _table(tableId);
    const reply = CompanionReply(
      mode: 'chatty',
      transcript: 'Demo transcript',
      shouldInterrupt: false,
      source: 'companion',
      content: 'Demo reply: front-end flow is connected.',
    );
    table.context.add(
      const ContextEventRecord(
        kind: 'assistant_spoken',
        source: 'companion',
        content: 'Demo reply: front-end flow is connected.',
      ),
    );
    return reply;
  }

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async {
    final table = _table(tableId);
    const content =
        'Demo interrupt accepted. The current playback is marked unsaid.';
    table.context.add(
      const ContextEventRecord(
        kind: 'assistant_interrupted',
        source: 'companion',
        content: content,
      ),
    );
    return const CompanionInterruptResult(
      interrupt: true,
      mode: 'serious',
      source: 'companion',
      content: content,
      speechAccepted: true,
      speechJobId: 'demo-job-1',
      ttsStreamId: 'demo-stream-1',
    );
  }

  @override
  Future<List<ContextEventRecord>> listContext(
      {required String tableId}) async {
    return List<ContextEventRecord>.unmodifiable(_table(tableId).context);
  }

  @override
  Future<List<TtsJobRecord>> listTtsJobs({required String tableId}) async {
    _table(tableId);
    return const [
      TtsJobRecord(
        jobId: 'demo-job-1',
        content: 'Demo spoken reply',
        mode: 'chatty',
        format: 'mp3',
        accepted: true,
        status: 'ready',
      ),
    ];
  }

  @override
  Future<RuntimeStateRecord> fetchRuntimeState(
      {required String tableId}) async {
    _table(tableId);
    return const RuntimeStateRecord(
      state: 'idle',
      isUserSpeaking: false,
      isAgentSpeaking: false,
      lastEvent: 'demo_ready',
      interrupted: false,
      queueDepth: 0,
      completedSegmentCount: 0,
    );
  }

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses({
    required String tableId,
  }) async {
    _table(tableId);
    return const [
      RuleAnalysisRecord(
        analysisId: 'demo-rule-1',
        tableId: 'demo-table-1',
        query: 'Can I evade after engaging?',
        ackText: 'Checking demo rules.',
        status: 'completed',
        result: CompanionReply(
          mode: 'serious',
          transcript: '',
          shouldInterrupt: false,
          source: 'rule_analysis',
          content: 'Demo rule result: yes, if the action window allows it.',
        ),
      ),
    ];
  }

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics({
    required String tableId,
  }) async {
    _table(tableId);
    return const LiveDiagnosticsRecord(
      websocketConnects: 1,
      websocketDisconnects: 0,
      audioChunksReceived: 12,
      audioBytesReceived: 768,
      draftTranscriptsForwarded: 2,
      stableTranscriptsForwarded: 1,
      finalTranscriptsForwarded: 1,
      realtimeReconnects: 0,
    );
  }

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {
    _table(tableId);
  }

  @override
  Future<void> markTtsJobInterrupted({
    required String tableId,
    required String jobId,
  }) async {
    _table(tableId).context.add(
          ContextEventRecord(
            kind: 'assistant_interrupted',
            source: 'demo',
            content: 'Demo job $jobId was interrupted.',
          ),
        );
  }

  @override
  Future<void> markTtsJobPlayed({
    required String tableId,
    required String jobId,
  }) async {
    _table(tableId).context.add(
          ContextEventRecord(
            kind: 'assistant_spoken',
            source: 'companion',
            content: 'Demo job $jobId finished playing.',
          ),
        );
  }

  @override
  Future<TtsSegmentRecord?> fetchNextTtsSegment({
    required String tableId,
    required String jobId,
  }) async {
    _table(tableId);
    return const TtsSegmentRecord(
      index: 0,
      text: 'Demo spoken reply',
      status: 'queued',
      format: 'mp3',
      outputPath: 'browser-memory',
    );
  }

  @override
  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    _table(tableId);
  }

  @override
  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    _table(tableId);
  }

  @override
  Future<TtsStreamRecord> startTtsStream({
    required String tableId,
    required String jobId,
  }) async {
    _table(tableId);
    final streamId = 'demo-stream-$jobId';
    _streamOffsets[streamId] = 0;
    return TtsStreamRecord(
      streamId: streamId,
      jobId: jobId,
      state: 'streaming',
      segmentCount: 1,
    );
  }

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async {
    _table(tableId);
    final offset = _streamOffsets[streamId];
    if (offset == null || offset > 0) {
      return null;
    }
    _streamOffsets[streamId] = offset + 1;
    return TtsStreamChunkRecord(
      streamId: streamId,
      jobId: streamId.replaceFirst('demo-stream-', ''),
      chunkIndex: 0,
      segmentIndex: 0,
      text: 'Demo spoken reply',
      audioBytes: const [1, 2, 3, 4],
      isFinal: true,
    );
  }

  @override
  Future<void> cancelTtsStream({
    required String tableId,
    required String streamId,
  }) async {
    _table(tableId);
    _streamOffsets.remove(streamId);
  }

  @override
  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    _table(tableId);
    return const [1, 2, 3, 4];
  }

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
  }) async {
    _table(tableId);
    return ReadResult(
      kind: 'document_summary',
      mode: 'summary',
      content: 'Demo summary for $query.',
    );
  }

  @override
  Future<void> deleteTable(String tableId) async {
    _tables.remove(tableId);
  }

  @override
  Future<String> renameTable(String tableId, String name) async {
    _table(tableId).name = name;
    return name;
  }
}

class _DemoTable {
  _DemoTable({
    required this.id,
    required this.name,
    required this.assistantName,
    required this.createdAt,
    required this.documents,
    required this.context,
  });

  final String id;
  String name;
  String assistantName;
  final String createdAt;
  final List<DocumentRecord> documents;
  final List<ContextEventRecord> context;

  TableRecord toRecord() {
    return TableRecord(
      id: id,
      name: name,
      status: 'active',
      assistantName: assistantName,
    );
  }

  TableListItem toListItem() {
    return TableListItem(
      id: id,
      name: name,
      assistantName: assistantName,
      status: 'active',
      createdAt: createdAt,
      lastActiveAt: DateTime.now().toIso8601String(),
      personalityPreview: 'Browser demo personality',
      documentCount: documents.length,
      documentTotalBytes: documents.fold<int>(
        0,
        (total, document) => total + document.sizeBytes,
      ),
    );
  }
}

List<DocumentRecord> _demoDocuments() {
  return const [
    DocumentRecord(
      filename: 'demo-scenario.txt',
      status: 'stored',
      sizeBytes: 2048,
    ),
    DocumentRecord(
      filename: 'demo-rules.txt',
      status: 'stored',
      sizeBytes: 1024,
    ),
  ].toList();
}

String _uniqueDocumentName(String filename, Set<String> existingNames) {
  if (!existingNames.contains(filename)) {
    return filename;
  }

  final dotIndex = filename.lastIndexOf('.');
  final stem = dotIndex > 0 ? filename.substring(0, dotIndex) : filename;
  final extension = dotIndex > 0 ? filename.substring(dotIndex) : '';
  var index = 1;
  while (existingNames.contains('$stem ($index)$extension')) {
    index += 1;
  }
  return '$stem ($index)$extension';
}
