import 'package:audioplayers/audioplayers.dart';

class VoicePreviewPlayer {
  VoicePreviewPlayer({Uri? baseUri}) : _player = AudioPlayer() {
    _baseUri = baseUri;
  }

  final AudioPlayer _player;
  Uri? _baseUri;

  Future<void> play(String filename) async {
    try {
      if (_baseUri != null) {
        final uri = _baseUri!.replace(path: '/voice-previews/$filename');
        await _player.play(UrlSource(uri.toString()));
      } else {
        await _player.play(AssetSource(filename));
      }
    } catch (_) {
      // Playback not available in test environment; silently skip
    }
  }

  Future<void> stop() async {
    await _player.stop();
  }

  void dispose() {
    _player.dispose();
  }
}