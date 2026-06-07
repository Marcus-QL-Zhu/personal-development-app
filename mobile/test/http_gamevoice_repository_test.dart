import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/backend/http_gamevoice_repository.dart';

void main() {
  test('http repository sends bearer token when configured', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(server.close);

    server.listen((request) async {
      expect(request.headers.value(HttpHeaders.authorizationHeader),
          'Bearer app-token');
      request.response
        ..statusCode = 200
        ..headers.contentType = ContentType.json
        ..write(jsonEncode({'status': 'ok'}));
      await request.response.close();
    });

    final repository = HttpGameVoiceRepository(
      baseUri: Uri.parse('http://${server.address.host}:${server.port}'),
      apiToken: 'app-token',
    );

    expect(await repository.healthCheck(), isTrue);
  });

  test('uploadFiles sends non-ascii filenames with RFC 5987 encoding', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(server.close);

    server.listen((request) async {
      if (request.method == 'POST' && request.uri.path == '/tables/table-1/documents') {
        final body = await request.fold<List<int>>(<int>[], (buffer, chunk) {
          buffer.addAll(chunk);
          return buffer;
        });
        final text = ascii.decode(body, allowInvalid: true);
        expect(text, contains('filename="upload.pdf"'));
        expect(
          text,
          contains("filename*=UTF-8''2_%E9%9D%A2%E8%AF%95%E4%B8%80%E9%A1%B5%E7%BA%B8_%E5%9D%87%E5%8C%80%E5%88%86%E5%B8%83%E6%BB%A1%E7%89%88%E8%A1%A8%E6%A0%BC.pdf"),
        );
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'notifications': 1,
            'message': '我看到你刚刚传了 1 个文件：2_面试一页纸_均匀分布满版表格.pdf。要看详情的话，点开一个文件名就行。',
            'records': const [
              {
                'filename': '2_面试一页纸_均匀分布满版表格.pdf',
                'status': 'stored',
                'size_bytes': 4,
              },
            ],
          }));
        await request.response.close();
        return;
      }

      request.response.statusCode = 404;
      await request.response.close();
    });

    final repository = HttpGameVoiceRepository(
      baseUri: Uri.parse('http://${server.address.host}:${server.port}'),
    );

    final result = await repository.uploadFiles(
      tableId: 'table-1',
      files: const [
        UploadFilePayload(
          filename: '2_面试一页纸_均匀分布满版表格.pdf',
          bytes: [1, 2, 3, 4],
        ),
      ],
    );

    expect(result.records.single.filename, '2_面试一页纸_均匀分布满版表格.pdf');
  });

  test('http repository matches the backend table and document flow', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(server.close);

    server.listen((request) async {
      if (request.method == 'POST' && request.uri.path == '/tables') {
        final body = jsonDecode(await utf8.decoder.bind(request).join()) as Map<String, dynamic>;
        expect(body['name'], 'Arkham table');
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({'id': 'table-1', 'name': 'Arkham table', 'status': 'active'}));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/documents') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'documents': [
              {'filename': 'scenario-a.txt', 'status': 'stored'},
            ],
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' && request.uri.path == '/tables/table-1/documents') {
        final body = await request.fold<List<int>>(<int>[], (buffer, chunk) {
          buffer.addAll(chunk);
          return buffer;
        });
        final text = utf8.decode(body, allowMalformed: true);
        expect(text, contains('filename="scenario-a.txt"'));
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'notifications': 1,
            'message': '我看到你刚刚传了 1 个文件：scenario-a.txt。要看详情的话，点开一个文件名就行。',
            'records': const [
              {'filename': 'scenario-a.txt', 'status': 'stored', 'size_bytes': 3},
            ],
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' && request.uri.path == '/tables/table-1/audio-clips') {
        final body = await request.fold<List<int>>(<int>[], (buffer, chunk) {
          buffer.addAll(chunk);
          return buffer;
        });
        final text = utf8.decode(body, allowMalformed: true);
        expect(text, contains('filename="round-1.wav"'));
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'kind': 'voice_transcript',
            'filename': 'round-1.wav',
            'content': 'Received voice clip round-1.wav.',
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' &&
          request.uri.path == '/tables/table-1/documents/scenario-a.txt/read') {
        expect(request.uri.queryParameters['mode'], 'summary');
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'kind': 'document_summary',
            'mode': 'summary',
            'content': 'Short summary',
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/companion/next') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'mode': 'chatty',
            'transcript': '先处理这个敌人吧',
            'should_interrupt': false,
            'reply': {
              'source': 'minimax',
              'content': '我先记下这句：先处理这个敌人吧',
            },
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' && request.uri.path == '/tables/table-1/companion/interrupt') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'interrupt': true,
            'mode': 'serious',
            'reply': {
              'source': 'minimax',
              'content': '规则答案：此时不能触发该效果。',
            },
            'speech_job': {
              'accepted': true,
              'job_id': 'job-1',
              'status': 'ready',
              'text': '规则答案：此时不能触发该效果。',
            },
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/interrupt') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'job': {'job_id': 'job-1', 'status': 'interrupted'}
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/played') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'job': {'job_id': 'job-1', 'status': 'played'}
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/segments/next') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'job_id': 'job-1',
            'segment': {
              'index': 0,
              'text': '规则答案：此时不能触发该效果。',
              'status': 'queued',
              'format': 'mp3',
              'output_path': '/tmp/job-1-segment-0.mp3',
            },
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/segments/0/started') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'segment': {'index': 0, 'status': 'playing'}
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/segments/0/completed') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'segment': {'index': 0, 'status': 'completed'}
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' &&
          request.uri.path == '/tables/table-1/tts-jobs/job-1/segments/0/audio') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType('audio', 'mpeg')
          ..add([1, 2, 3, 4]);
        await request.response.close();
        return;
      }

      if (request.method == 'POST' && request.uri.path == '/tables/table-1/tts-jobs/job-1/stream') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'stream_id': 'stream-1',
            'job_id': 'job-1',
            'state': 'streaming',
            'segment_count': 2,
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/tts-streams/stream-1/next') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'stream_id': 'stream-1',
            'job_id': 'job-1',
            'chunk_index': 0,
            'segment_index': 0,
            'text': '规则答案：此时不能触发该效果。',
            'is_final': false,
            'audio_base64': base64Encode([5, 6, 7]),
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' && request.uri.path == '/tables/table-1/tts-streams/stream-1/cancel') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'stream_id': 'stream-1',
            'job_id': 'job-1',
            'state': 'cancelled',
            'segment_count': 2,
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/context') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'events': [
              {
                'kind': 'voice_transcript',
                'source': 'live_asr',
                'content': '先处理这个敌人吧',
              },
              {
                'kind': 'assistant_reply',
                'source': 'companion',
                'content': '规则答案：此时不能触发该效果。',
              },
            ],
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/tts-jobs') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'jobs': [
              {
                'job_id': 'job-1',
                'content': '规则答案：此时不能触发该效果。',
                'mode': 'serious',
                'format': 'mp3',
                'accepted': true,
                'status': 'ready',
              },
            ],
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/tables/table-1/runtime/state') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({
            'state': 'agent_speaking',
            'is_user_speaking': false,
            'is_agent_speaking': true,
            'last_event': 'agent_speaking_started',
            'interrupted': false,
          }));
        await request.response.close();
        return;
      }

      if (request.method == 'POST' &&
          request.uri.path == '/tables/table-1/mobile-diagnostics') {
        final body = jsonDecode(await utf8.decoder.bind(request).join())
            as Map<String, dynamic>;
        final entries = body['entries'] as List<dynamic>;
        expect(entries, hasLength(1));
        expect(entries.single['component'], 'table_shell');
        expect(entries.single['event'], 'live_start_requested');
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({'accepted': 1, 'total': 1}));
        await request.response.close();
        return;
      }

      if (request.method == 'GET' && request.uri.path == '/health') {
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.json
          ..write(jsonEncode({'status': 'ok'}));
        await request.response.close();
        return;
      }

      request.response.statusCode = 404;
      await request.response.close();
    });

    final repository = HttpGameVoiceRepository(
      baseUri: Uri.parse('http://${server.address.address}:${server.port}'),
    );

    final table = await repository.createTable('Arkham table');
    final documents = await repository.listDocuments(table.id);
    final upload = await repository.uploadFiles(
      tableId: table.id,
      files: const [
        UploadFilePayload(filename: 'scenario-a.txt', bytes: [1, 2, 3]),
      ],
    );
    final healthy = await repository.healthCheck();
    final transcript = await repository.uploadVoiceClip(
      tableId: table.id,
      clip: const UploadFilePayload(filename: 'round-1.wav', bytes: [7, 8, 9]),
    );
    final summary = await repository.readDocumentSummary(
      tableId: table.id,
      query: 'scenario-a.txt',
    );
    final companion = await repository.fetchCompanionReply(tableId: table.id);
    final interrupt = await repository.runCompanionInterrupt(tableId: table.id);
    final context = await repository.listContext(tableId: table.id);
    final ttsJobs = await repository.listTtsJobs(tableId: table.id);
    final runtime = await repository.fetchRuntimeState(tableId: table.id);
    await repository.uploadMobileDiagnostics(
      tableId: table.id,
      entries: const [
        MobileDiagnosticEntry(
          ts: '2026-05-10T10:40:00.000Z',
          sessionId: 'live-session-1',
          component: 'table_shell',
          event: 'live_start_requested',
          details: {'route': 'table_shell'},
        ),
      ],
    );
    await repository.markTtsJobInterrupted(tableId: table.id, jobId: 'job-1');
    await repository.markTtsJobPlayed(tableId: table.id, jobId: 'job-1');
    final nextSegment = await repository.fetchNextTtsSegment(tableId: table.id, jobId: 'job-1');
    final stream = await repository.startTtsStream(tableId: table.id, jobId: 'job-1');
    final nextChunk = await repository.fetchNextTtsStreamChunk(
      tableId: table.id,
      streamId: 'stream-1',
    );
    await repository.cancelTtsStream(tableId: table.id, streamId: 'stream-1');
    final segmentAudio = await repository.fetchTtsSegmentAudioBytes(
      tableId: table.id,
      jobId: 'job-1',
      segmentIndex: 0,
    );
    await repository.markTtsSegmentStarted(tableId: table.id, jobId: 'job-1', segmentIndex: 0);
    await repository.markTtsSegmentCompleted(tableId: table.id, jobId: 'job-1', segmentIndex: 0);
    final latestTtsUrl = repository.latestTtsAudioUri(tableId: table.id);
    final segmentTtsUrl = repository.ttsSegmentAudioUri(
      tableId: table.id,
      jobId: 'job-1',
      segmentIndex: 0,
    );

    expect(table.name, 'Arkham table');
    expect(upload.message, contains('scenario-a.txt'));
    expect(documents.single.filename, 'scenario-a.txt');
    expect(healthy, isTrue);
    expect(transcript.filename, 'round-1.wav');
    expect(transcript.content, contains('round-1.wav'));
    expect(summary.content, 'Short summary');
    expect(companion.mode, 'chatty');
    expect(companion.content, contains('先处理这个敌人吧'));
    expect(interrupt.interrupt, isTrue);
    expect(interrupt.speechAccepted, isTrue);
    expect(interrupt.speechJobId, 'job-1');
    expect(context.length, 2);
    expect(context.first.content, '先处理这个敌人吧');
    expect(ttsJobs.single.jobId, 'job-1');
    expect(ttsJobs.single.status, 'ready');
    expect(ttsJobs.single.content, contains('规则答案'));
    expect(runtime.state, 'agent_speaking');
    expect(runtime.isAgentSpeaking, isTrue);
    expect(nextSegment, isNotNull);
    expect(nextSegment!.index, 0);
    expect(nextSegment.text, contains('规则答案'));
    expect(stream.streamId, 'stream-1');
    expect(stream.segmentCount, 2);
    expect(nextChunk, isNotNull);
    expect(nextChunk!.segmentIndex, 0);
    expect(nextChunk.audioBytes, <int>[5, 6, 7]);
    expect(segmentAudio, <int>[1, 2, 3, 4]);
    expect(latestTtsUrl.toString(), contains('/tables/table-1/tts-jobs/latest/audio'));
    expect(segmentTtsUrl.toString(), contains('/tables/table-1/tts-jobs/job-1/segments/0/audio'));
  });
}
