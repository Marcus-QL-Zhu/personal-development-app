import 'dart:convert';
import 'dart:io';

import '../backend/gamevoice_repository.dart';
import 'development_repository.dart';

class HttpDevelopmentRepository implements DevelopmentRepository {
  HttpDevelopmentRepository({
    required this.baseUri,
    String? apiToken,
    HttpClient? httpClient,
  })  : _apiToken = apiToken?.trim() ?? '',
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
  Future<List<DevelopmentEmployee>> listEmployees() async {
    final request = await _openRequest('GET', '/development/employees');
    final response = await _readJson(request);
    final employees = response['employees'] as List<dynamic>? ?? const [];
    return employees
        .whereType<Map<String, dynamic>>()
        .map(DevelopmentEmployee.fromJson)
        .toList();
  }

  @override
  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path: '/development/employees',
      payload: {
        'name': name,
        'gallup_raw': gallupRaw,
        'profile_note': profileNote,
      },
    );
    return DevelopmentEmployee.fromJson(response);
  }

  @override
  Future<DevelopmentEmployee> updateEmployee({
    required String employeeId,
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final response = await _sendJson(
      method: 'PUT',
      path: '/development/employees/$employeeId',
      payload: {
        'name': name,
        'gallup_raw': gallupRaw,
        'profile_note': profileNote,
      },
    );
    return DevelopmentEmployee.fromJson(response);
  }

  @override
  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  }) async {
    final request = await _openRequest(
      'GET',
      '/development/employees/$employeeId/coaching-sessions',
    );
    final response = await _readJson(request);
    final sessions = response['sessions'] as List<dynamic>? ?? const [];
    return sessions
        .whereType<Map<String, dynamic>>()
        .map(DevelopmentCoachingSession.fromJson)
        .toList();
  }

  @override
  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    if (clip.localPath.isNotEmpty) {
      return _uploadLocalRecordingViaFlashAsr(
          employeeId: employeeId, clip: clip);
    }
    final boundary = 'coach-audio-${DateTime.now().microsecondsSinceEpoch}';
    final request = await _openRequest(
      'POST',
      '/development/employees/$employeeId/coaching-sessions',
    );
    request.headers.set(
      HttpHeaders.contentTypeHeader,
      'multipart/form-data; boundary=$boundary',
    );

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
    return DevelopmentCoachingSession.fromJson(response);
  }

  Future<DevelopmentCoachingSession> _uploadLocalRecordingViaFlashAsr({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    final chunkPaths =
        clip.chunkPaths.isNotEmpty ? clip.chunkPaths : [clip.localPath];
    final transcripts = <Map<String, dynamic>>[];
    for (final chunkPath in chunkPaths) {
      final file = File(chunkPath);
      if (!await file.exists()) {
        throw FileSystemException('Recording file not found', chunkPath);
      }
      final size = await file.length();
      final filename = file.uri.pathSegments.isEmpty
          ? clip.filename
          : file.uri.pathSegments.last;
      final signature = await _withRetries(
        () => _createFlashAsrSignature(filename: filename, contentLength: size),
      );
      transcripts.add(
          await _withRetries(() => _postToTencentFlashAsr(file, signature)));
    }

    final merged = _mergeTencentTranscripts(transcripts);
    final session = await _withRetries(
      () => _createCoachingSessionFromTranscript(
        employeeId: employeeId,
        clip: clip,
        transcript: merged,
      ),
    );
    for (final chunkPath in chunkPaths) {
      await File(chunkPath).delete().catchError((_) => File(chunkPath));
    }
    return session;
  }

  Future<Map<String, dynamic>> _createFlashAsrSignature({
    required String filename,
    required int contentLength,
  }) {
    return _sendJson(
      method: 'POST',
      path: '/development/asr/flash-signatures',
      payload: {
        'filename': filename,
        'content_length': contentLength,
      },
    );
  }

  Future<Map<String, dynamic>> _postToTencentFlashAsr(
    File file,
    Map<String, dynamic> signature,
  ) async {
    final request = await _httpClient.openUrl(
      signature['method'] as String? ?? 'POST',
      Uri.parse(signature['url'] as String),
    );
    final headers = signature['headers'] as Map<String, dynamic>? ?? const {};
    for (final entry in headers.entries) {
      request.headers.set(entry.key, entry.value.toString());
    }
    request.headers.set(HttpHeaders.contentLengthHeader, await file.length());
    request.add(await file.readAsBytes());
    return _readJson(request);
  }

  Future<DevelopmentCoachingSession> _createCoachingSessionFromTranscript({
    required String employeeId,
    required UploadFilePayload clip,
    required Map<String, dynamic> transcript,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path:
          '/development/employees/$employeeId/coaching-sessions/from-transcript',
      payload: {
        'recording_id': clip.recordingId,
        'audio_filename': clip.filename,
        'transcript_text': transcript['text'] as String? ?? '',
        'segments': transcript['segments'] as List<dynamic>? ?? const [],
        'asr_provider': 'tencent_flash_asr_mobile',
        'quality_status': transcript['quality_status'] as String? ?? 'ok',
        'asr_error': transcript['error'] as String? ?? '',
      },
    );
    return DevelopmentCoachingSession.fromJson(response);
  }

  Map<String, dynamic> _mergeTencentTranscripts(
      List<Map<String, dynamic>> payloads) {
    final texts = <String>[];
    final segments = <Map<String, dynamic>>[];
    for (final payload in payloads) {
      if (payload['code'] != null && payload['code'] != 0) {
        throw HttpException('Tencent Flash ASR failed: ${jsonEncode(payload)}');
      }
      for (final result
          in (payload['flash_result'] as List<dynamic>? ?? const [])) {
        if (result is! Map<String, dynamic>) continue;
        final text = result['text'] as String? ?? '';
        if (text.isNotEmpty) texts.add(text);
        for (final sentence
            in (result['sentence_list'] as List<dynamic>? ?? const [])) {
          if (sentence is Map<String, dynamic>) {
            final sentenceText = sentence['text'] as String? ?? '';
            if (sentenceText.isNotEmpty) {
              segments.add({
                'speaker_id': '${sentence['speaker_id'] ?? ''}',
                'text': sentenceText,
                'start_time': sentence['start_time'],
                'end_time': sentence['end_time'],
              });
            }
          }
        }
      }
    }
    return {
      'text': texts.join(),
      'segments': segments,
      'quality_status':
          texts.isEmpty && segments.isEmpty ? 'quality_pending' : 'ok',
    };
  }

  Future<T> _withRetries<T>(Future<T> Function() operation) async {
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await operation();
      } catch (e) {
        lastError = e;
        if (attempt < 2) {
          await Future<void>.delayed(
              Duration(milliseconds: 250 * (attempt + 1)));
        }
      }
    }
    throw lastError ?? StateError('operation failed');
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

  Future<HttpClientRequest> _openRequest(String method, String path) {
    final uri = baseUri.replace(path: path);
    return _httpClient.openUrl(method, uri).then((request) {
      if (_apiToken.isNotEmpty) {
        request.headers
            .set(HttpHeaders.authorizationHeader, 'Bearer $_apiToken');
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
}

String _multipartContentDisposition({
  required String fieldName,
  required String filename,
}) {
  final encoded = Uri.encodeComponent(filename);
  return 'Content-Disposition: form-data; name="$fieldName"; filename="upload.bin"; filename*=UTF-8\'\'$encoded\r\n';
}
