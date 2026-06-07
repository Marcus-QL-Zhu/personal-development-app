import 'dart:async';

import 'package:flutter/foundation.dart';

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

class NetworkTtsAudioPlayer implements TtsAudioPlayer {
  NetworkTtsAudioPlayer();

  final StreamController<TtsPlaybackEvent> _eventsController =
      StreamController<TtsPlaybackEvent>.broadcast();
  String? _lastSavedPath;

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
    _lastSavedPath = 'browser-memory:${bytes.length}';
    _emit('prepared', 'browser-memory', 'TTS bytes accepted by web test player');
    _emit('playing', 'browser-memory', 'Web test player started');
    await Future<void>.delayed(const Duration(milliseconds: 40));
    _emit('completed', 'browser-memory', 'Web test player completed');
    onCompleted?.call();
  }

  @override
  Future<void> stop() async {
    _emit('stopped', 'browser-memory', 'Stop requested');
  }

  void _emit(String state, String engine, String message) {
    debugPrint('[TTS][web] $state $message');
    if (!_eventsController.isClosed) {
      _eventsController.add(
        TtsPlaybackEvent(
          state: state,
          engine: engine,
          message: message,
          path: _lastSavedPath,
        ),
      );
    }
  }
}
