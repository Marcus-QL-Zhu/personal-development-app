import 'package:audio_session/audio_session.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/audio/audio_session_policy.dart';

void main() {
  test('buildGameVoiceAudioSessionConfiguration prefers playAndRecord speech', () {
    final config = GameVoiceAudioSessionPolicy().buildConfiguration();

    expect(config.avAudioSessionCategory, AVAudioSessionCategory.playAndRecord);
    expect(config.avAudioSessionMode, AVAudioSessionMode.spokenAudio);
    expect(config.androidAudioAttributes?.usage, AndroidAudioUsage.voiceCommunication);
    expect(config.androidAudioAttributes?.contentType, AndroidAudioContentType.speech);
    expect(config.androidWillPauseWhenDucked, isTrue);
  });
}
