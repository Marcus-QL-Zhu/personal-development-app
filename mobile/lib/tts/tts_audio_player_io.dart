import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart' as ap;
import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart' as ja;
import 'package:path_provider/path_provider.dart';

abstract class TtsAudioPlayer {
  String? get lastSavedPath;
  Stream<TtsPlaybackEvent> get events;

  Future<void> playBytes(
    List<int> bytes, {
    VoidCallback? onCompleted,
  });

  Future<void> stop();
}

typedef VoidCallback = void Function();

class TtsPlaybackEvent {
  const TtsPlaybackEvent({
    required this.state,
    required this.engine,
    this.message,
    this.path,
  });

  final String state;
  final String engine;
  final String? message;
  final String? path;
}

abstract class TtsPrimaryPlaybackEngine {
  Stream<String> get onLog;
  Stream<void> get onPlayerComplete;

  Future<void> setAudioContext(ap.AudioContext context);
  Future<void> setVolume(double volume);
  Future<void> setSource(ap.DeviceFileSource source);
  Future<void> resume();
  Future<void> stop();
}

abstract class TtsFallbackPlaybackEngine {
  Stream<ja.PlayerState> get playerStateStream;
  ja.ProcessingState get processingState;

  Future<void> setFilePath(String path);
  Future<void> play();
  Future<void> stop();
}

class _AudioPlayersPrimaryEngine implements TtsPrimaryPlaybackEngine {
  _AudioPlayersPrimaryEngine(this._player);

  final ap.AudioPlayer _player;

  @override
  Stream<String> get onLog => _player.onLog;

  @override
  Stream<void> get onPlayerComplete => _player.onPlayerComplete;

  @override
  Future<void> setAudioContext(ap.AudioContext context) =>
      _player.setAudioContext(context);

  @override
  Future<void> setVolume(double volume) => _player.setVolume(volume);

  @override
  Future<void> setSource(ap.DeviceFileSource source) =>
      _player.setSource(source);

  @override
  Future<void> resume() => _player.resume();

  @override
  Future<void> stop() => _player.stop();
}

class _JustAudioFallbackEngine implements TtsFallbackPlaybackEngine {
  _JustAudioFallbackEngine(this._player);

  final ja.AudioPlayer _player;

  @override
  Stream<ja.PlayerState> get playerStateStream => _player.playerStateStream;

  @override
  ja.ProcessingState get processingState => _player.processingState;

  @override
  Future<void> setFilePath(String path) => _player.setFilePath(path);

  @override
  Future<void> play() => _player.play();

  @override
  Future<void> stop() => _player.stop();
}

class NetworkTtsAudioPlayer implements TtsAudioPlayer {
  NetworkTtsAudioPlayer({
    TtsPrimaryPlaybackEngine? primaryEngine,
    TtsFallbackPlaybackEngine? fallbackEngine,
    Future<Directory> Function()? documentsDirectoryProvider,
    Duration primaryTimeout = const Duration(seconds: 5),
  })  : _primaryEngine =
            primaryEngine ?? _AudioPlayersPrimaryEngine(ap.AudioPlayer()),
        _fallbackEngine =
            fallbackEngine ?? _JustAudioFallbackEngine(ja.AudioPlayer()),
        _documentsDirectoryProvider =
            documentsDirectoryProvider ?? getApplicationDocumentsDirectory,
        _primaryTimeout = primaryTimeout;

  final TtsPrimaryPlaybackEngine _primaryEngine;
  final TtsFallbackPlaybackEngine _fallbackEngine;
  final Future<Directory> Function() _documentsDirectoryProvider;
  final Duration _primaryTimeout;
  final StreamController<TtsPlaybackEvent> _eventsController =
      StreamController<TtsPlaybackEvent>.broadcast();
  String? _lastSavedPath;

  static final ap.AudioContext _duplexSafeAudioContext = ap.AudioContext(
    android: const ap.AudioContextAndroid(
      isSpeakerphoneOn: true,
      audioMode: ap.AndroidAudioMode.inCommunication,
      usageType: ap.AndroidUsageType.voiceCommunication,
      contentType: ap.AndroidContentType.speech,
      audioFocus: ap.AndroidAudioFocus.gain,
      stayAwake: false,
    ),
    iOS: ap.AudioContextIOS(
      category: ap.AVAudioSessionCategory.playback,
      options: const {
        ap.AVAudioSessionOptions.mixWithOthers,
      },
    ),
  );

  @override
  String? get lastSavedPath => _lastSavedPath;

  @override
  Stream<TtsPlaybackEvent> get events => _eventsController.stream;

  @override
  Future<void> playBytes(
    List<int> bytes, {
    VoidCallback? onCompleted,
  }) async {
    if (bytes.isEmpty) {
      throw StateError('TTS bytes are empty');
    }
    final audioPath = await _persistMp3(bytes);
    _lastSavedPath = audioPath;
    _emit(
      state: 'prepared',
      engine: 'file',
      message: 'TTS chunk persisted',
      path: audioPath,
    );

    await _applyPrimaryAudioRoute();
    _emit(
      state: 'route_prepared',
      engine: 'audioplayers',
      message: 'Primary audio route reasserted',
      path: audioPath,
    );

    try {
      _emit(
        state: 'playing',
        engine: 'audioplayers',
        message: 'Primary engine start',
        path: audioPath,
      );
      await _playWithPrimaryEngine(audioPath);
      _emit(
        state: 'completed',
        engine: 'audioplayers',
        message: 'Primary engine completed',
        path: audioPath,
      );
      onCompleted?.call();
      return;
    } catch (error, stackTrace) {
      final state = error is TimeoutException ? 'timeout' : 'error';
      _emit(
        state: state,
        engine: 'audioplayers',
        message: '$error',
        path: audioPath,
      );
      debugPrint('[TTS] audioplayers playback failed: $error');
      debugPrintStack(stackTrace: stackTrace);
      rethrow;
    }
  }

  @override
  Future<void> stop() async {
    await _fallbackEngine.stop();
    await _primaryEngine.stop();
    _emit(
      state: 'stopped',
      engine: 'player',
      message: 'Stop requested',
      path: _lastSavedPath,
    );
  }

  Future<String> _persistMp3(List<int> bytes) async {
    final docs = await _documentsDirectoryProvider();
    final dir = Directory(
      '${docs.path}${Platform.pathSeparator}gamevoice_tts',
    );
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    final filename =
        'gamevoice-tts-${DateTime.now().microsecondsSinceEpoch}.mp3';
    final file = File('${dir.path}${Platform.pathSeparator}$filename');
    await file.writeAsBytes(Uint8List.fromList(bytes), flush: true);
    return file.path;
  }

  Future<void> _applyPrimaryAudioRoute() async {
    await _primaryEngine.setAudioContext(_duplexSafeAudioContext);
    await _primaryEngine.setVolume(1.0);
  }

  Future<void> _playWithPrimaryEngine(String path) async {
    await _primaryEngine.stop();
    final completed = Completer<void>();
    late final StreamSubscription<void> completionSubscription;
    completionSubscription = _primaryEngine.onPlayerComplete.listen((_) {
      if (!completed.isCompleted) {
        completed.complete();
      }
    });
    try {
      await Future<void>(() async {
        await _primaryEngine.setSource(
          ap.DeviceFileSource(path, mimeType: 'audio/mpeg'),
        );
        await _primaryEngine.resume();
      }).timeout(
        _primaryTimeout,
        onTimeout: () async {
          await _primaryEngine.stop();
          throw TimeoutException('Primary playback start timed out');
        },
      );
      await completed.future;
    } finally {
      await completionSubscription.cancel();
      await _primaryEngine.stop();
    }
  }

  void _emit({
    required String state,
    required String engine,
    String? message,
    String? path,
  }) {
    if (_eventsController.isClosed) {
      return;
    }
    _eventsController.add(
      TtsPlaybackEvent(
        state: state,
        engine: engine,
        message: message,
        path: path,
      ),
    );
  }
}
