import 'package:flutter_test/flutter_test.dart';
import 'package:record/record.dart';

import 'package:personal_development_app/audio/voice_recorder.dart';

void main() {
  test('android voice chat config uses duplex communication mode', () {
    final config = buildAndroidVoiceChatConfig();

    expect(config.audioSource, AndroidAudioSource.voiceCommunication);
    expect(config.audioManagerMode, AudioManagerMode.modeInCommunication);
    expect(config.manageBluetooth, isTrue);
  });
}
