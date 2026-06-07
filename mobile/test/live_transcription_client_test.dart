import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/live/live_transcription_client.dart';

void main() {
  test('live websocket client sends access token query parameter', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(server.close);
    final seenUris = <Uri>[];

    server.listen((request) async {
      seenUris.add(request.uri);
      final socket = await WebSocketTransformer.upgrade(request);
      socket.add(jsonEncode({'event': 'final', 'text': 'ok'}));
      await socket.close();
    });

    final client = WsLiveTranscriptionClient(
      backendLabel: 'http://${server.address.host}:${server.port}',
      apiToken: 'app-token',
    );

    await client.connect(
      tableId: 'table-1',
      onEvent: (_) {},
    );
    await client.close();

    expect(seenUris.single.path, '/ws/tables/table-1/listen');
    expect(seenUris.single.queryParameters['access_token'], 'app-token');
  });
}
