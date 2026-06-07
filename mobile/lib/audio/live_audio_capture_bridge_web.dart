abstract class LiveAudioCaptureBridge {
  bool get isSupported;

  Future<Stream<List<int>>> startLiveCapture();

  Future<void> stopLiveCapture();
}

class PlatformLiveAudioCaptureBridge implements LiveAudioCaptureBridge {
  @override
  bool get isSupported => false;

  @override
  Future<Stream<List<int>>> startLiveCapture() async {
    throw UnsupportedError('Native live audio capture is not available on web.');
  }

  @override
  Future<void> stopLiveCapture() async {}
}
