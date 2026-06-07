import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:just_audio/just_audio.dart' as ja;
import 'package:audioplayers/audioplayers.dart' as ap;
import 'dart:io';

import 'package:personal_development_app/tts/tts_audio_player.dart';

class FakePrimaryEngine implements TtsPrimaryPlaybackEngine {
  final Completer<void> configured = Completer<void>();
  final StreamController<void> _complete = StreamController<void>.broadcast();
  final StreamController<String> _log = StreamController<String>.broadcast();
  Completer<void>? resumeGate;
  ap.AudioContext? lastAudioContext;
  int audioContextCalls = 0;
  int setSourceCalls = 0;
  int resumeCalls = 0;
  bool resumeCalled = false;
  bool completeOnResume = true;

  @override
  Stream<String> get onLog => _log.stream;

  @override
  Stream<void> get onPlayerComplete => _complete.stream;

  @override
  Future<void> setAudioContext(ap.AudioContext context) async {
    audioContextCalls += 1;
    lastAudioContext = context;
    await configured.future;
  }

  @override
  Future<void> setVolume(double volume) async {}

  @override
  Future<void> stop() async {}

  @override
  Future<void> setSource(ap.DeviceFileSource source) async {
    setSourceCalls += 1;
  }

  @override
  Future<void> resume() async {
    resumeCalled = true;
    resumeCalls += 1;
    if (resumeGate != null) {
      await resumeGate!.future;
    }
    if (completeOnResume) {
      _complete.add(null);
    }
  }
}

class FakeFallbackEngine implements TtsFallbackPlaybackEngine {
  final StreamController<ja.PlayerState> _states =
      StreamController<ja.PlayerState>.broadcast();
  bool stopped = false;
  bool completeImmediately = false;
  String? lastFilePath;
  int playCalls = 0;

  @override
  Stream<ja.PlayerState> get playerStateStream => _states.stream;

  @override
  Future<void> stop() async {
    stopped = true;
  }

  @override
  Future<void> setFilePath(String path) async {
    lastFilePath = path;
  }

  @override
  Future<void> play() async {
    playCalls += 1;
    if (completeImmediately) {
      _states.add(
        ja.PlayerState(false, ja.ProcessingState.completed),
      );
    }
  }

  @override
  ja.ProcessingState get processingState => ja.ProcessingState.idle;
}

void main() {
  test('tts playback uses communication audio context for duplex playback', () async {
    final primary = FakePrimaryEngine();
    final fallback = FakeFallbackEngine();
    final tempDir = await Directory.systemTemp.createTemp('tts-audio-test');
    final player = NetworkTtsAudioPlayer(
      primaryEngine: primary,
      fallbackEngine: fallback,
      documentsDirectoryProvider: () async => tempDir,
    );

    primary.configured.complete();
    await player.playBytes([1, 2, 3]);

    final androidContext = primary.lastAudioContext?.android;
    expect(androidContext, isNotNull);
    expect(androidContext!.isSpeakerphoneOn, isTrue);
    expect(androidContext.audioMode, ap.AndroidAudioMode.inCommunication);
    expect(androidContext.usageType, ap.AndroidUsageType.voiceCommunication);
    expect(androidContext.contentType, ap.AndroidContentType.speech);
    expect(androidContext.audioFocus, ap.AndroidAudioFocus.gain);
  });

  test('tts playback waits for audio context setup before starting playback', () async {
    final primary = FakePrimaryEngine();
    final fallback = FakeFallbackEngine();
    final tempDir = await Directory.systemTemp.createTemp('tts-audio-test');
    final player = NetworkTtsAudioPlayer(
      primaryEngine: primary,
      fallbackEngine: fallback,
      documentsDirectoryProvider: () async => tempDir,
      primaryTimeout: Duration.zero,
    );

    final playFuture = player.playBytes([1, 2, 3]);
    await Future<void>.delayed(Duration.zero);

    primary.configured.complete();
    await playFuture;
    expect(primary.setSourceCalls, 1);
    expect(primary.resumeCalls, 1);
    expect(primary.resumeCalled, isTrue);
  });

  test('tts playback reapplies audio context before each playback attempt', () async {
    final primary = FakePrimaryEngine();
    final fallback = FakeFallbackEngine();
    final tempDir = await Directory.systemTemp.createTemp('tts-audio-test');
    final player = NetworkTtsAudioPlayer(
      primaryEngine: primary,
      fallbackEngine: fallback,
      documentsDirectoryProvider: () async => tempDir,
    );

    primary.configured.complete();
    await player.playBytes([1, 2, 3]);
    await player.playBytes([4, 5, 6]);

    expect(primary.audioContextCalls, 2);
    expect(primary.setSourceCalls, 2);
    expect(primary.resumeCalls, 2);
  });

  test('tts playback emits route diagnostics before playback starts', () async {
    final primary = FakePrimaryEngine();
    final fallback = FakeFallbackEngine();
    final tempDir = await Directory.systemTemp.createTemp('tts-audio-test');
    final player = NetworkTtsAudioPlayer(
      primaryEngine: primary,
      fallbackEngine: fallback,
      documentsDirectoryProvider: () async => tempDir,
    );

    final states = <String>[];
    final subscription = player.events.listen((event) {
      states.add(event.state);
    });
    primary.configured.complete();

    await player.playBytes([1, 2, 3]);
    await subscription.cancel();

    expect(states, containsAllInOrder(['prepared', 'route_prepared', 'playing']));
  });

  test('tts playback surfaces a primary start timeout without fallback replay', () async {
    final primary = FakePrimaryEngine();
    primary.completeOnResume = false;
    primary.resumeGate = Completer<void>();
    final fallback = FakeFallbackEngine()..completeImmediately = true;
    final tempDir = await Directory.systemTemp.createTemp('tts-audio-test');
    final player = NetworkTtsAudioPlayer(
      primaryEngine: primary,
      fallbackEngine: fallback,
      documentsDirectoryProvider: () async => tempDir,
      primaryTimeout: Duration.zero,
    );

    primary.configured.complete();

    final states = <String>[];
    final engines = <String>[];
    final subscription = player.events.listen((event) {
      states.add(event.state);
      engines.add(event.engine);
    });

    await expectLater(
      player.playBytes([1, 2, 3]),
      throwsA(isA<TimeoutException>()),
    );
    await Future<void>.delayed(Duration.zero);
    await subscription.cancel();

    expect(states, contains('timeout'));
    expect(states, contains('route_prepared'));
    expect(engines, isNot(contains('just_audio')));
    expect(fallback.playCalls, 0);
    expect(fallback.lastFilePath, isNull);
  });
}
