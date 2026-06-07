import 'dart:convert';
import 'dart:io';

import 'gamevoice_repository.dart';

class HttpGameVoiceRepository implements GameVoiceRepository {
  HttpGameVoiceRepository({
    required this.baseUri,
    String? apiToken,
    HttpClient? httpClient,
  }) : _apiToken = apiToken?.trim() ?? '',
       _httpClient = httpClient ?? HttpClient();

  final Uri baseUri;
  final String _apiToken;
  final HttpClient _httpClient;

  @override
  Future<bool> healthCheck() async {
    final request = await _openRequest('GET', '/health');
    final response = await _readJson(request);
    return response['status'] == 'ok';
  }

  @override
  Future<List<TableListItem>> listTables() async {
    final request = await _openRequest('GET', '/tables');
    final response = await _readJson(request);
    final tables = response['tables'] as List<dynamic>? ?? const [];
    return tables.map((t) {
      final m = t as Map<String, dynamic>;
      return TableListItem(
        id: m['id'] as String,
        name: m['name'] as String,
        assistantName: m['assistant_name'] as String,
        status: m['status'] as String,
        createdAt: m['created_at'] as String? ?? '',
        lastActiveAt: m['last_active_at'] as String? ?? '',
        personalityPreview: m['personality_preview'] as String? ?? '',
        documentCount: m['document_count'] as int? ?? 0,
        documentTotalBytes: m['document_total_bytes'] as int? ?? 0,
      );
    }).toList();
  }

  @override
  Future<TableRecord> createTable(
    String name, {
    String? assistantName,
    String? assistantPersonality,
    String? assistantVoiceId,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path: '/tables',
      payload: {
        'name': name,
        if (assistantName != null) 'assistant_name': assistantName,
        if (assistantPersonality != null)
          'assistant_personality': assistantPersonality,
        if (assistantVoiceId != null) 'assistant_voice_id': assistantVoiceId,
      },
    );
    return TableRecord(
      id: response['id'] as String,
      name: response['name'] as String,
      status: response['status'] as String,
      assistantName: response['assistant_name'] as String? ?? '宝子',
    );
  }

  @override
  Future<String> fetchAssistantName({
    required String tableId,
  }) async {
    final request =
        await _openRequest('GET', '/tables/$tableId/assistant-profile');
    final response = await _readJson(request);
    return response['assistant_name'] as String? ?? '宝子';
  }

  @override
  Future<String> updateAssistantName({
    required String tableId,
    required String assistantName,
  }) async {
    final response = await _sendJson(
      method: 'PUT',
      path: '/tables/$tableId/assistant-profile',
      payload: {'assistant_name': assistantName},
    );
    return response['assistant_name'] as String? ?? assistantName;
  }

  @override
  Future<List<DocumentRecord>> listDocuments(String tableId) async {
    final request = await _openRequest('GET', '/tables/$tableId/documents');
    final response = await _readJson(request);
    final documents = response['documents'] as List<dynamic>? ?? const [];
    return documents
        .map(
          (item) => DocumentRecord(
            filename: item['filename'] as String,
            status: item['status'] as String,
            sizeBytes: item['size_bytes'] as int? ?? 0,
          ),
        )
        .toList();
  }

  @override
  Future<DocumentUploadResult> uploadFiles({
    required String tableId,
    required List<UploadFilePayload> files,
  }) async {
    final boundary =
        'gamevoice-boundary-${DateTime.now().microsecondsSinceEpoch}';
    final request = await _openRequest('POST', '/tables/$tableId/documents');
    request.headers.set(HttpHeaders.contentTypeHeader,
        'multipart/form-data; boundary=$boundary');

    for (final file in files) {
      request.write('--$boundary\r\n');
      request.write(_multipartContentDisposition(
        fieldName: 'files',
        filename: file.filename,
      ));
      request.write('Content-Type: application/octet-stream\r\n\r\n');
      request.add(file.bytes);
      request.write('\r\n');
    }
    request.write('--$boundary--\r\n');

    final response = await request.close();
    if (response.statusCode >= 400) {
      throw HttpException('Upload failed: ${response.statusCode}');
    }
    final body = await utf8.decodeStream(response);
    final payload = jsonDecode(body) as Map<String, dynamic>;
    final records = payload['records'] as List<dynamic>? ?? const [];
    return DocumentUploadResult(
      message: payload['message'] as String? ?? '文件上传成功',
      records: records
          .map(
            (item) => DocumentRecord(
              filename: item['filename'] as String,
              status: item['status'] as String,
              sizeBytes: item['size_bytes'] as int? ?? 0,
            ),
          )
          .toList(),
    );
  }

  @override
  Future<void> deleteDocument({
    required String tableId,
    required String filename,
  }) async {
    final request = await _openRequest(
      'DELETE',
      '/tables/$tableId/documents/${Uri.encodeComponent(filename)}',
    );
    await _readJson(request);
  }

  @override
  Future<VoiceTranscript> uploadVoiceClip({
    required String tableId,
    required UploadFilePayload clip,
  }) async {
    final boundary = 'gamevoice-audio-${DateTime.now().microsecondsSinceEpoch}';
    final request = await _openRequest('POST', '/tables/$tableId/audio-clips');
    request.headers.set(HttpHeaders.contentTypeHeader,
        'multipart/form-data; boundary=$boundary');

    request.write('--$boundary\r\n');
    request.write(_multipartContentDisposition(
      fieldName: 'clip',
      filename: clip.filename,
    ));
    request.write('Content-Type: application/octet-stream\r\n\r\n');
    request.add(clip.bytes);
    request.write('\r\n');
    request.write('--$boundary--\r\n');

    final response = await _readJson(request);
    return VoiceTranscript(
      kind: response['kind'] as String,
      filename: response['filename'] as String,
      content: response['content'] as String,
    );
  }

  @override
  Future<CompanionReply> fetchCompanionReply({
    required String tableId,
  }) async {
    final request =
        await _openRequest('GET', '/tables/$tableId/companion/next');
    final response = await _readJson(request);
    final reply = response['reply'] as Map<String, dynamic>? ?? const {};
    return CompanionReply(
      mode: response['mode'] as String? ?? 'idle',
      transcript: response['transcript'] as String? ?? '',
      shouldInterrupt: response['should_interrupt'] as bool? ?? false,
      source: reply['source'] as String? ?? 'companion',
      content: reply['content'] as String? ?? '',
      lead: reply['lead'] as String?,
      tail: reply['tail'] as String?,
      turnId: response['turn_id'] as String?,
      replyId: response['reply_id'] as String?,
    );
  }

  @override
  Future<CompanionInterruptResult> runCompanionInterrupt({
    required String tableId,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/companion/interrupt',
      payload: const {},
    );
    final reply = response['reply'] as Map<String, dynamic>? ?? const {};
    final speechJob = response['speech_job'] as Map<String, dynamic>?;
    return CompanionInterruptResult(
      interrupt: response['interrupt'] as bool? ?? false,
      mode: response['mode'] as String? ?? 'idle',
      source: reply['source'] as String? ?? 'companion',
      content: reply['content'] as String? ?? '',
      lead: reply['lead'] as String?,
      tail: reply['tail'] as String?,
      turnId: response['turn_id'] as String?,
      replyId: response['reply_id'] as String?,
      speechAccepted: speechJob?['accepted'] as bool? ?? false,
      speechJobId: speechJob?['job_id'] as String?,
      ttsStreamId: (response['tts_stream']
          as Map<String, dynamic>?)?['stream_id'] as String?,
    );
  }

  @override
  Future<List<ContextEventRecord>> listContext({
    required String tableId,
  }) async {
    final request = await _openRequest('GET', '/tables/$tableId/context');
    final response = await _readJson(request);
    final events = response['events'] as List<dynamic>? ?? const [];
    return events
        .map(
          (item) => ContextEventRecord(
            kind: item['kind'] as String? ?? 'unknown',
            source: item['source'] as String? ?? 'unknown',
            content: item['content'] as String? ?? '',
          ),
        )
        .toList();
  }

  @override
  Future<List<TtsJobRecord>> listTtsJobs({
    required String tableId,
  }) async {
    final request = await _openRequest('GET', '/tables/$tableId/tts-jobs');
    final response = await _readJson(request);
    final jobs = response['jobs'] as List<dynamic>? ?? const [];
    return jobs
        .map(
          (item) => TtsJobRecord(
            jobId: item['job_id'] as String? ?? '',
            content: item['content'] as String? ?? '',
            mode: item['mode'] as String? ?? 'chatty',
            format: item['format'] as String? ?? 'mp3',
            accepted: item['accepted'] as bool? ?? false,
            status: item['status'] as String? ?? 'ready',
            turnId: item['turn_id'] as String?,
            replyId: item['reply_id'] as String?,
          ),
        )
        .toList();
  }

  @override
  Future<RuntimeStateRecord> fetchRuntimeState({
    required String tableId,
  }) async {
    final request = await _openRequest('GET', '/tables/$tableId/runtime/state');
    final response = await _readJson(request);
    return RuntimeStateRecord(
      state: response['state'] as String? ?? 'listening',
      isUserSpeaking: response['is_user_speaking'] as bool? ?? false,
      isAgentSpeaking: response['is_agent_speaking'] as bool? ?? false,
      lastEvent: response['last_event'] as String? ?? 'unknown',
      interrupted: response['interrupted'] as bool? ?? false,
      currentJobId: response['current_job_id'] as String?,
      pendingReplyText: response['pending_reply_text'] as String?,
      previewReplyText: response['preview_reply_text'] as String?,
      previewSourceText: response['preview_source_text'] as String?,
      lastCompletedJobId: response['last_completed_job_id'] as String?,
      queueDepth: response['queue_depth'] as int?,
      currentSegmentIndex: response['current_segment_index'] as int?,
      completedSegmentCount: response['completed_segment_count'] as int?,
    );
  }

  @override
  Future<List<RuleAnalysisRecord>> listRuleAnalyses({
    required String tableId,
  }) async {
    final request =
        await _openRequest('GET', '/tables/$tableId/rules/analyses');
    final response = await _readJson(request);
    final analyses = response['analyses'] as List<dynamic>? ?? const [];
    return analyses
        .whereType<Map<String, dynamic>>()
        .map(_parseRuleAnalysisRecord)
        .toList();
  }

  @override
  Future<LiveDiagnosticsRecord> fetchLiveDiagnostics({
    required String tableId,
  }) async {
    final request =
        await _openRequest('GET', '/tables/$tableId/live-diagnostics');
    final response = await _readJson(request);
    return LiveDiagnosticsRecord(
      websocketConnects: response['websocket_connects'] as int? ?? 0,
      websocketDisconnects: response['websocket_disconnects'] as int? ?? 0,
      audioChunksReceived: response['audio_chunks_received'] as int? ?? 0,
      audioBytesReceived: response['audio_bytes_received'] as int? ?? 0,
      audioReceiveMonotonicMs:
          (response['audio_receive_monotonic_ms'] as num?)?.toDouble(),
      audioInterArrivalMs:
          (response['audio_inter_arrival_ms'] as num?)?.toDouble(),
      maxAudioInterArrivalMs:
          (response['max_audio_inter_arrival_ms'] as num?)?.toDouble(),
      receiveBurstCount: response['receive_burst_count'] as int? ?? 0,
      maxReceiveBurstChunksPerSecond:
          response['max_receive_burst_chunks_per_second'] as int? ?? 0,
      audioQueueDepthOnEnqueue:
          response['audio_queue_depth_on_enqueue'] as int?,
      audioQueueDepthOnDequeue:
          response['audio_queue_depth_on_dequeue'] as int?,
      sendWorkerLagMs: (response['send_worker_lag_ms'] as num?)?.toDouble(),
      maxSendWorkerLagMs:
          (response['max_send_worker_lag_ms'] as num?)?.toDouble(),
      sendAudioElapsedMs:
          (response['send_audio_elapsed_ms'] as num?)?.toDouble(),
      maxSendAudioElapsedMs:
          (response['max_send_audio_elapsed_ms'] as num?)?.toDouble(),
      tencentPayloadSendElapsedMs:
          (response['tencent_payload_send_elapsed_ms'] as num?)?.toDouble(),
      maxTencentPayloadSendElapsedMs:
          (response['max_tencent_payload_send_elapsed_ms'] as num?)?.toDouble(),
      sendAudioPacingRequestedMs:
          (response['send_audio_pacing_requested_ms'] as num?)?.toDouble(),
      sendAudioPacingActualMs:
          (response['send_audio_pacing_actual_ms'] as num?)?.toDouble(),
      maxSendAudioPacingActualMs:
          (response['max_send_audio_pacing_actual_ms'] as num?)?.toDouble(),
      eventLoopLagMs: (response['event_loop_lag_ms'] as num?)?.toDouble(),
      maxEventLoopLagMs:
          (response['max_event_loop_lag_ms'] as num?)?.toDouble(),
      lastEventLoopLagAt: response['last_event_loop_lag_at'] as String?,
      draftTranscriptsForwarded:
          response['draft_transcripts_forwarded'] as int? ?? 0,
      stableTranscriptsForwarded:
          response['stable_transcripts_forwarded'] as int? ?? 0,
      finalTranscriptsForwarded:
          response['final_transcripts_forwarded'] as int? ?? 0,
      realtimeReconnects: response['realtime_reconnects'] as int? ?? 0,
      silenceGateState: response['silence_gate_state'] as String?,
      silenceGatePassedChunks:
          response['silence_gate_passed_chunks'] as int? ?? 0,
      silenceGateSuppressedChunks:
          response['silence_gate_suppressed_chunks'] as int? ?? 0,
      silenceGateSuppressedBytes:
          response['silence_gate_suppressed_bytes'] as int? ?? 0,
      silenceGatePrerollFlushes:
          response['silence_gate_preroll_flushes'] as int? ?? 0,
      silenceGateLastDecision:
          response['silence_gate_last_decision'] as Map<String, dynamic>?,
      silenceGateLastError: response['silence_gate_last_error'] as String?,
      lastAudioChunkAt: response['last_audio_chunk_at'] as String?,
      lastDraftTranscriptAt: response['last_draft_transcript_at'] as String?,
      lastStableTranscriptAt: response['last_stable_transcript_at'] as String?,
      lastFinalTranscriptAt: response['last_final_transcript_at'] as String?,
      lastReconnectAt: response['last_reconnect_at'] as String?,
      lastError: response['last_error'] as String?,
    );
  }

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {
    if (entries.isEmpty) {
      return;
    }
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/mobile-diagnostics',
      payload: {
        'entries': entries.map((entry) => entry.toJson()).toList(),
      },
    );
  }

  @override
  Future<void> markTtsJobInterrupted({
    required String tableId,
    required String jobId,
  }) async {
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-jobs/$jobId/interrupt',
      payload: const {},
    );
  }

  @override
  Future<void> markTtsJobPlayed({
    required String tableId,
    required String jobId,
  }) async {
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-jobs/$jobId/played',
      payload: const {},
    );
  }

  @override
  Future<TtsSegmentRecord?> fetchNextTtsSegment({
    required String tableId,
    required String jobId,
  }) async {
    final request = await _openRequest(
        'GET', '/tables/$tableId/tts-jobs/$jobId/segments/next');
    final response = await request.close();
    final body = await utf8.decodeStream(response);
    if (response.statusCode == 404) {
      return null;
    }
    if (response.statusCode >= 400) {
      throw HttpException('Request failed: ${response.statusCode} $body');
    }
    final payload = jsonDecode(body) as Map<String, dynamic>;
    final segment = payload['segment'] as Map<String, dynamic>;
    return TtsSegmentRecord(
      index: segment['index'] as int? ?? 0,
      text: segment['text'] as String? ?? '',
      status: segment['status'] as String? ?? 'queued',
      format: segment['format'] as String? ?? 'mp3',
      outputPath: segment['output_path'] as String? ?? '',
    );
  }

  @override
  Future<void> markTtsSegmentStarted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/started',
      payload: const {},
    );
  }

  @override
  Future<void> markTtsSegmentCompleted({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/completed',
      payload: const {},
    );
  }

  @override
  Future<TtsStreamRecord> startTtsStream({
    required String tableId,
    required String jobId,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-jobs/$jobId/stream',
      payload: const {},
    );
    return TtsStreamRecord(
      streamId: response['stream_id'] as String? ?? '',
      jobId: response['job_id'] as String? ?? '',
      state: response['state'] as String? ?? 'streaming',
      segmentCount: response['segment_count'] as int? ?? 0,
      turnId: response['turn_id'] as String?,
      replyId: response['reply_id'] as String?,
    );
  }

  @override
  Future<TtsStreamChunkRecord?> fetchNextTtsStreamChunk({
    required String tableId,
    required String streamId,
  }) async {
    final request = await _openRequest(
        'GET', '/tables/$tableId/tts-streams/$streamId/next');
    final response = await request.close();
    final body = await utf8.decodeStream(response);
    if (response.statusCode >= 400) {
      throw HttpException('Request failed: ${response.statusCode} $body');
    }
    final payload = jsonDecode(body) as Map<String, dynamic>;
    return TtsStreamChunkRecord(
      streamId: payload['stream_id'] as String? ?? '',
      jobId: payload['job_id'] as String? ?? '',
      chunkIndex: payload['chunk_index'] as int? ?? 0,
      segmentIndex: payload['segment_index'] as int? ?? 0,
      text: payload['text'] as String? ?? '',
      audioBytes: base64Decode(payload['audio_base64'] as String? ?? ''),
      isFinal: payload['is_final'] as bool? ?? false,
      turnId: payload['turn_id'] as String?,
      replyId: payload['reply_id'] as String?,
    );
  }

  @override
  Future<void> cancelTtsStream({
    required String tableId,
    required String streamId,
  }) async {
    await _sendJson(
      method: 'POST',
      path: '/tables/$tableId/tts-streams/$streamId/cancel',
      payload: const {},
    );
  }

  @override
  Future<List<int>> fetchTtsSegmentAudioBytes({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) async {
    final request = await _openRequest(
      'GET',
      '/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/audio',
    );
    final response = await request.close();
    final bytes = await response.fold<List<int>>(<int>[], (buffer, chunk) {
      buffer.addAll(chunk);
      return buffer;
    });
    if (response.statusCode >= 400) {
      throw HttpException('Request failed: ${response.statusCode}');
    }
    return bytes;
  }

  @override
  Uri latestTtsAudioUri({
    required String tableId,
  }) {
    return _withAccessToken(
      baseUri.replace(path: '/tables/$tableId/tts-jobs/latest/audio'),
    );
  }

  @override
  Uri ttsSegmentAudioUri({
    required String tableId,
    required String jobId,
    required int segmentIndex,
  }) {
    return _withAccessToken(
      baseUri.replace(
          path: '/tables/$tableId/tts-jobs/$jobId/segments/$segmentIndex/audio'),
    );
  }

  @override
  Uri voicePreviewUri(String filename) {
    return _withAccessToken(baseUri.replace(path: '/voice-previews/$filename'));
  }

  @override
  Future<ReadResult> readDocumentSummary({
    required String tableId,
    required String query,
  }) async {
    final request = await _openRequest(
      'GET',
      '/tables/$tableId/documents/${Uri.encodeComponent(query)}/read',
      queryParameters: const {'mode': 'summary'},
    );
    final response = await _readJson(request);
    return ReadResult(
      kind: response['kind'] as String,
      mode: response['mode'] as String,
      content: response['content'] as String,
    );
  }

  @override
  Future<void> deleteTable(String tableId) async {
    await _sendJson(
      method: 'DELETE',
      path: '/tables/$tableId',
      payload: const {},
    );
  }

  @override
  Future<String> renameTable(String tableId, String name) async {
    final response = await _sendJson(
      method: 'PATCH',
      path: '/tables/$tableId',
      payload: {'name': name},
    );
    return response['name'] as String? ?? name;
  }

  Future<Map<String, dynamic>> _sendJson({
    required String method,
    required String path,
    required Map<String, dynamic> payload,
  }) async {
    final request = await _openRequest(method, path);
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode(payload));
    return _readJson(request);
  }

  Future<HttpClientRequest> _openRequest(
    String method,
    String path, {
    Map<String, String>? queryParameters,
  }) {
    final uri = baseUri.replace(
      path: path,
      queryParameters: queryParameters,
    );
    return _httpClient.openUrl(method, uri).then((request) {
      if (_apiToken.isNotEmpty) {
        request.headers.set(
          HttpHeaders.authorizationHeader,
          'Bearer $_apiToken',
        );
      }
      return request;
    });
  }

  Future<Map<String, dynamic>> _readJson(HttpClientRequest request) async {
    final response = await request.close();
    final body = await utf8.decodeStream(response);
    if (response.statusCode >= 400) {
      throw HttpException('Request failed: ${response.statusCode} $body');
    }
    return jsonDecode(body) as Map<String, dynamic>;
  }

  RuleAnalysisRecord _parseRuleAnalysisRecord(Map<String, dynamic> item) {
    final result = item['result'] as Map<String, dynamic>?;
    return RuleAnalysisRecord(
      analysisId: item['analysis_id'] as String? ?? '',
      tableId: item['table_id'] as String? ?? '',
      query: item['query'] as String? ?? '',
      ackText: item['ack_text'] as String? ?? '',
      status: item['status'] as String? ?? 'queued',
      result: result == null
          ? null
          : CompanionReply(
              mode: result['mode'] as String? ?? 'serious',
              transcript: result['transcript'] as String? ?? '',
              shouldInterrupt: false,
              source: result['source'] as String? ?? 'rule_analysis',
              content: result['content'] as String? ?? '',
              lead: result['lead'] as String?,
              tail: result['tail'] as String?,
              turnId: result['turn_id'] as String?,
              replyId: result['reply_id'] as String?,
            ),
      error: item['error'] as String?,
    );
  }

  Uri _withAccessToken(Uri uri) {
    if (_apiToken.isEmpty) {
      return uri;
    }
    return uri.replace(
      queryParameters: {
        ...uri.queryParameters,
        'access_token': _apiToken,
      },
    );
  }
}

String _multipartContentDisposition({
  required String fieldName,
  required String filename,
}) {
  final quotedField = _quoteHeaderValue(fieldName);
  if (_isAsciiHeaderValue(filename)) {
    return 'Content-Disposition: form-data; name="$quotedField"; filename="${_quoteHeaderValue(filename)}"\r\n';
  }

  final fallback = _asciiFallbackFilename(filename);
  final encoded = Uri.encodeComponent(filename);
  return 'Content-Disposition: form-data; name="$quotedField"; filename="$fallback"; filename*=UTF-8\'\'$encoded\r\n';
}

bool _isAsciiHeaderValue(String value) {
  if (value.isEmpty) {
    return false;
  }
  return value.codeUnits.every((unit) => unit >= 0x20 && unit <= 0x7e);
}

String _quoteHeaderValue(String value) {
  return value.replaceAll(r'\', r'\\').replaceAll('"', r'\"');
}

String _asciiFallbackFilename(String filename) {
  final dotIndex = filename.lastIndexOf('.');
  final extension = dotIndex >= 0 ? filename.substring(dotIndex) : '';
  final safeExtension = RegExp(r'^\.[A-Za-z0-9]{1,12}$').hasMatch(extension)
      ? extension
      : '.bin';
  return 'upload$safeExtension';
}
