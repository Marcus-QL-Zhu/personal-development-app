import 'dart:async';

import 'package:audio_session/audio_session.dart';
import 'package:flutter/widgets.dart';

class GameVoiceAudioSessionPolicy {
  StreamSubscription<AudioInterruptionEvent>? _interruptionSubscription;
  bool _disposed = false;

  AudioSessionConfiguration buildConfiguration() {
    return AudioSessionConfiguration(
      avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
      avAudioSessionCategoryOptions:
          AVAudioSessionCategoryOptions.allowBluetooth |
              AVAudioSessionCategoryOptions.defaultToSpeaker,
      avAudioSessionMode: AVAudioSessionMode.spokenAudio,
      avAudioSessionRouteSharingPolicy:
          AVAudioSessionRouteSharingPolicy.defaultPolicy,
      avAudioSessionSetActiveOptions: AVAudioSessionSetActiveOptions.none,
      androidAudioAttributes: const AndroidAudioAttributes(
        contentType: AndroidAudioContentType.speech,
        flags: AndroidAudioFlags.none,
        usage: AndroidAudioUsage.voiceCommunication,
      ),
      androidAudioFocusGainType: AndroidAudioFocusGainType.gain,
      androidWillPauseWhenDucked: true,
    );
  }

  Future<AudioSession> configure() async {
    final session = await AudioSession.instance;
    await session.configure(buildConfiguration());
    if (!_disposed && _interruptionSubscription == null) {
      _interruptionSubscription = session.interruptionEventStream.listen(
        (event) {
          if (event.begin || _disposed) {
            return;
          }
          unawaited(session.configure(buildConfiguration()));
        },
      );
    }
    return session;
  }

  Future<void> dispose() async {
    _disposed = true;
    await _interruptionSubscription?.cancel();
    _interruptionSubscription = null;
  }
}

class GameVoiceAudioSessionHost extends StatefulWidget {
  const GameVoiceAudioSessionHost({
    super.key,
    required this.policy,
    required this.child,
  });

  final GameVoiceAudioSessionPolicy policy;
  final Widget child;

  @override
  State<GameVoiceAudioSessionHost> createState() =>
      _GameVoiceAudioSessionHostState();
}

class _GameVoiceAudioSessionHostState extends State<GameVoiceAudioSessionHost> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.policy.configure());
  }

  @override
  void dispose() {
    unawaited(widget.policy.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
