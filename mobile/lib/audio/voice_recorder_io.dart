import 'dart:io';

import 'package:flutter/foundation.dart';
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
  })  : _backend = backend ?? RecordPluginBackend(),
        _liveCaptureBridge = liveCaptureBridge ?? PlatformLiveAudioCaptureBridge(),
        _preferNativeLiveCapture = preferNativeLiveCapture ?? Platform.isAndroid;

  final RecorderBackend _backend;
  final LiveAudioCaptureBridge _liveCaptureBridge;
  final bool _preferNativeLiveCapture;
  String? _activePath;
  bool _streamMode = false;
  bool _usingNativeLiveCapture = false;

  RecordConfig _buildClipConfig() {
    return RecordConfig(
      encoder: AudioEncoder.wav,
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
    final tempDir = Directory.systemTemp;
    final path = '${tempDir.path}${Platform.pathSeparator}gamevoice-${DateTime.now().millisecondsSinceEpoch}.wav';
    await _backend.start(
      _buildClipConfig(),
      path: path,
    );
    _activePath = path;
  }

  @override
  Future<UploadFilePayload?> stop() async {
    final path = await _backend.stop();
    _streamMode = false;
    _usingNativeLiveCapture = false;
    final resolvedPath = path ?? _activePath;
    _activePath = null;
    if (resolvedPath == null) {
      return null;
    }

    final file = File(resolvedPath);
    if (!await file.exists()) {
      return null;
    }

    final bytes = await file.readAsBytes();
    await file.delete().catchError((_) => file);
    return UploadFilePayload(
      filename: file.uri.pathSegments.isEmpty ? 'voice-clip.wav' : file.uri.pathSegments.last,
      bytes: bytes,
    );
  }

  @override
  Future<Stream<List<int>>> startLiveStream() async {
    _streamMode = true;
    if (_preferNativeLiveCapture && _liveCaptureBridge.isSupported) {
      _usingNativeLiveCapture = true;
      debugPrint('[LIVE][recorder] using native live capture');
      MobileDiagnostics.record(component: 'recorder', event: 'using_native_live_capture');
      return _liveCaptureBridge.startLiveCapture();
    }
    _usingNativeLiveCapture = false;
    debugPrint('[LIVE][recorder] using record plugin live stream');
    MobileDiagnostics.record(component: 'recorder', event: 'using_record_plugin_live_stream');
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
