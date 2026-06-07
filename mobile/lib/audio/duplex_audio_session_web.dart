abstract class DuplexAudioSession {
  Future<void> activate();

  Future<void> deactivate();
}

class PlatformDuplexAudioSession implements DuplexAudioSession {
  @override
  Future<void> activate() async {}

  @override
  Future<void> deactivate() async {}
}
