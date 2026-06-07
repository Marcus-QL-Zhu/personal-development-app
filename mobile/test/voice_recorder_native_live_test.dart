import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/audio/live_audio_capture_bridge.dart';
import 'package:personal_development_app/audio/voice_recorder.dart';
import 'package:record/record.dart';

class _FakeRecorderBackend implements RecorderBackend {
  final StreamController<List<int>> liveController =
      StreamController<List<int>>.broadcast();
  int startStreamCount = 0;
  int stopCount = 0;

  @override
  Future<void> dispose() async {
    await liveController.close();
  }

  @override
  Future<bool> hasPermission() async => true;

  @override
  Future<void> start(RecordConfig config, {required String path}) async {}

  @override
  Future<Stream<List<int>>> startStream(RecordConfig config) async {
    startStreamCount += 1;
    return liveController.stream;
  }

  @override
  Future<String?> stop() async {
    stopCount += 1;
    return null;
  }
}

class _FakeLiveAudioCaptureBridge implements LiveAudioCaptureBridge {
  _FakeLiveAudioCaptureBridge({required this.isSupported});

  @override
  final bool isSupported;

  final StreamController<List<int>> liveController =
      StreamController<List<int>>.broadcast();
  int startCount = 0;
  int stopCount = 0;

  @override
  Future<Stream<List<int>>> startLiveCapture() async {
    startCount += 1;
    return liveController.stream;
  }

  @override
  Future<void> stopLiveCapture() async {
    stopCount += 1;
  }
}

void main() {
  test('recorder prefers native live capture bridge when supported', () async {
    final backend = _FakeRecorderBackend();
    final bridge = _FakeLiveAudioCaptureBridge(isSupported: true);
    final recorder = RecordVoiceRecorder(
      backend: backend,
      liveCaptureBridge: bridge,
      preferNativeLiveCapture: true,
    );

    final stream = await recorder.startLiveStream();
    final chunks = <List<int>>[];
    final subscription = stream.listen(chunks.add);

    bridge.liveController.add(<int>[1, 2, 3]);
    await pumpEventQueue();

    expect(bridge.startCount, 1);
    expect(backend.startStreamCount, 0);
    expect(chunks, anyElement(equals(<int>[1, 2, 3])));

    await recorder.stopLiveStream();
    expect(bridge.stopCount, 1);

    await subscription.cancel();
    await recorder.dispose();
  });

  test('recorder falls back to record backend when native bridge unsupported',
      () async {
    final backend = _FakeRecorderBackend();
    final bridge = _FakeLiveAudioCaptureBridge(isSupported: false);
    final recorder = RecordVoiceRecorder(
      backend: backend,
      liveCaptureBridge: bridge,
      preferNativeLiveCapture: true,
    );

    final stream = await recorder.startLiveStream();
    final chunks = <List<int>>[];
    final subscription = stream.listen(chunks.add);

    backend.liveController.add(<int>[4, 5, 6]);
    await pumpEventQueue();

    expect(bridge.startCount, 0);
    expect(backend.startStreamCount, 1);
    expect(chunks, anyElement(equals(<int>[4, 5, 6])));

    await recorder.stopLiveStream();
    expect(bridge.stopCount, 0);
    expect(backend.stopCount, 1);

    await subscription.cancel();
    await recorder.dispose();
  });
}
