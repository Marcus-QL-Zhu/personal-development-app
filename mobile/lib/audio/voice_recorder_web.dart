import 'dart:async';

import '../backend/gamevoice_repository.dart';

abstract class VoiceRecorder {
  Future<bool> ensurePermission();

  Future<void> start();

  Future<UploadFilePayload?> stop();

  Future<Stream<List<int>>> startLiveStream();

  Future<void> stopLiveStream();

  Future<void> dispose();
}

class RecordVoiceRecorder implements VoiceRecorder {
  bool _recording = false;
  StreamController<List<int>>? _liveController;

  @override
  Future<bool> ensurePermission() async => true;

  @override
  Future<void> start() async {
    _recording = true;
  }

  @override
  Future<UploadFilePayload?> stop() async {
    if (!_recording) {
      return null;
    }
    _recording = false;
    return const UploadFilePayload(
      filename: 'browser-demo.wav',
      bytes: [82, 73, 70, 70],
    );
  }

  @override
  Future<Stream<List<int>>> startLiveStream() async {
    _liveController = StreamController<List<int>>();
    Timer.periodic(const Duration(milliseconds: 250), (timer) {
      final controller = _liveController;
      if (controller == null || controller.isClosed) {
        timer.cancel();
        return;
      }
      controller.add(const [0, 1, 0, 1]);
    });
    return _liveController!.stream;
  }

  @override
  Future<void> stopLiveStream() async {
    await _liveController?.close();
    _liveController = null;
  }

  @override
  Future<void> dispose() async {
    await stopLiveStream();
  }
}
