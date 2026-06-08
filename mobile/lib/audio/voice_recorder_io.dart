import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../backend/gamevoice_repository.dart';
import '../diagnostics/mobile_diagnostics_logger.dart';
import 'live_audio_capture_bridge.dart';

AndroidRecordConfig buildAndroidVoiceChatConfig() {
  return const AndroidRecordConfig(
    audioSource: AndroidAudioSource.voiceCommunication,
    audioManagerMode: AudioManagerMode.modeInCommunication,
    manageBluetooth: true,
  );
}

abstract class VoiceRecorder {
  Future<bool> ensurePermission();

  Future<void> start();

  Future<UploadFilePayload?> stop();

  Future<Stream<List<int>>> startLiveStream();

  Future<void> stopLiveStream();

  Future<void> dispose();
}

abstract class RecorderBackend {
  Future<bool> hasPermission();

  Future<void> start(RecordConfig config, {required String path});

  Future<String?> stop();

  Future<Stream<List<int>>> startStream(RecordConfig config);

  Future<void> dispose();
}

class RecordPluginBackend implements RecorderBackend {
  RecordPluginBackend({AudioRecorder? recorder})
      : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;

  @override
  Future<void> dispose() {
    return _recorder.dispose();
  }

  @override
  Future<bool> hasPermission() {
    return _recorder.hasPermission();
  }

  @override
  Future<void> start(RecordConfig config, {required String path}) {
    return _recorder.start(config, path: path);
  }

  @override
  Future<Stream<List<int>>> startStream(RecordConfig config) async {
    final stream = await _recorder.startStream(config);
    return stream.map((chunk) => chunk.toList());
  }

  @override
  Future<String?> stop() {
    return _recorder.stop();
  }
}

class RecordVoiceRecorder implements VoiceRecorder {
  RecordVoiceRecorder({
    RecorderBackend? backend,
    LiveAudioCaptureBridge? liveCaptureBridge,
    bool? preferNativeLiveCapture,
    Directory? recordingRoot,
    Duration? maxClipChunkDuration = const Duration(minutes: 55),
  })  : _backend = backend ?? RecordPluginBackend(),
        _liveCaptureBridge =
            liveCaptureBridge ?? PlatformLiveAudioCaptureBridge(),
        _preferNativeLiveCapture =
            preferNativeLiveCapture ?? Platform.isAndroid,
        _recordingRoot = recordingRoot,
        _maxClipChunkDuration = maxClipChunkDuration;

  final RecorderBackend _backend;
  final LiveAudioCaptureBridge _liveCaptureBridge;
  final bool _preferNativeLiveCapture;
  final Directory? _recordingRoot;
  final Duration? _maxClipChunkDuration;
  String? _activePath;
  String? _activeRecordingId;
  final List<String> _activeChunkPaths = [];
  Timer? _clipRotationTimer;
  bool _streamMode = false;
  bool _usingNativeLiveCapture = false;

  RecordConfig _buildClipConfig() {
    return RecordConfig(
      encoder: AudioEncoder.aacLc,
      sampleRate: 16000,
      numChannels: 1,
      androidConfig: buildAndroidVoiceChatConfig(),
      autoGain: true,
      echoCancel: true,
      noiseSuppress: true,
    );
  }

  RecordConfig _buildLiveConfig() {
    return RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
      androidConfig: buildAndroidVoiceChatConfig(),
      autoGain: true,
      echoCancel: true,
      noiseSuppress: true,
    );
  }

  @override
  Future<bool> ensurePermission() {
    return _backend.hasPermission().then((granted) {
      MobileDiagnostics.record(
        component: 'recorder',
        event: 'permission_result',
        details: {'granted': granted},
      );
      return granted;
    });
  }

  @override
  Future<void> start() async {
    final recordingId = DateTime.now().microsecondsSinceEpoch.toString();
    final root = _recordingRoot ?? await _defaultRecordingRoot();
    await root.create(recursive: true);
    final path = '${root.path}${Platform.pathSeparator}coach-$recordingId.m4a';
    await _backend.start(
      _buildClipConfig(),
      path: path,
    );
    _activePath = path;
    _activeRecordingId = recordingId;
    _activeChunkPaths
      ..clear()
      ..add(path);
    _scheduleClipRotation();
  }

  @override
  Future<UploadFilePayload?> stop() async {
    final path = await _backend.stop();
    _clipRotationTimer?.cancel();
    _clipRotationTimer = null;
    _streamMode = false;
    _usingNativeLiveCapture = false;
    final resolvedPath = path ?? _activePath;
    final recordingId = _activeRecordingId ?? '';
    _activePath = null;
    _activeRecordingId = null;
    if (resolvedPath == null) {
      return null;
    }

    final file = File(resolvedPath);
    if (!await file.exists()) {
      return null;
    }

    final bytes = await file.readAsBytes();
    return UploadFilePayload(
      filename: file.uri.pathSegments.isEmpty
          ? 'voice-clip.wav'
          : file.uri.pathSegments.last,
      bytes: bytes,
      localPath: file.path,
      recordingId: recordingId,
      chunkPaths: List.unmodifiable(_activeChunkPaths),
    );
  }

  void _scheduleClipRotation() {
    final duration = _maxClipChunkDuration;
    if (duration == null) return;
    _clipRotationTimer?.cancel();
    _clipRotationTimer = Timer(duration, () {
      _rotateClipChunk().catchError((Object error) {
        debugPrint('[RECORDER] clip rotation failed: $error');
        MobileDiagnostics.record(
          component: 'recorder',
          event: 'clip_rotation_failed',
          details: {'error': error.toString()},
        );
      });
    });
  }

  Future<void> _rotateClipChunk() async {
    final recordingId = _activeRecordingId;
    final root = _recordingRoot ?? await _defaultRecordingRoot();
    if (recordingId == null || _activePath == null) return;
    await _backend.stop();
    final nextIndex = _activeChunkPaths.length + 1;
    final nextPath =
        '${root.path}${Platform.pathSeparator}coach-$recordingId-$nextIndex.m4a';
    await _backend.start(_buildClipConfig(), path: nextPath);
    _activePath = nextPath;
    _activeChunkPaths.add(nextPath);
    _scheduleClipRotation();
  }

  Future<Directory> _defaultRecordingRoot() async {
    final docs = await getApplicationDocumentsDirectory();
    return Directory(
        '${docs.path}${Platform.pathSeparator}pending_coach_recordings');
  }

  @override
  Future<Stream<List<int>>> startLiveStream() async {
    _streamMode = true;
    if (_preferNativeLiveCapture && _liveCaptureBridge.isSupported) {
      _usingNativeLiveCapture = true;
      debugPrint('[LIVE][recorder] using native live capture');
      MobileDiagnostics.record(
          component: 'recorder', event: 'using_native_live_capture');
      return _liveCaptureBridge.startLiveCapture();
    }
    _usingNativeLiveCapture = false;
    debugPrint('[LIVE][recorder] using record plugin live stream');
    MobileDiagnostics.record(
        component: 'recorder', event: 'using_record_plugin_live_stream');
    return _backend.startStream(_buildLiveConfig());
  }

  @override
  Future<void> stopLiveStream() async {
    if (_streamMode) {
      debugPrint('[LIVE][recorder] stopping live stream');
      MobileDiagnostics.record(
        component: 'recorder',
        event: 'stopping_live_stream',
        details: {'using_native': _usingNativeLiveCapture},
      );
      if (_usingNativeLiveCapture) {
        await _liveCaptureBridge.stopLiveCapture();
      } else {
        await _backend.stop();
      }
      _streamMode = false;
      _usingNativeLiveCapture = false;
    }
  }

  @override
  Future<void> dispose() async {
    await _liveCaptureBridge.stopLiveCapture();
    await _backend.dispose();
  }
}
