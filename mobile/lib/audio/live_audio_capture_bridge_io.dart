import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../diagnostics/mobile_diagnostics_logger.dart';

abstract class LiveAudioCaptureBridge {
  bool get isSupported;

  Future<Stream<List<int>>> startLiveCapture();

  Future<void> stopLiveCapture();
}

class PlatformLiveAudioCaptureBridge implements LiveAudioCaptureBridge {
  PlatformLiveAudioCaptureBridge() {
    _methodChannel.setMethodCallHandler(_handleNativeMethodCall);
  }

  static const MethodChannel _methodChannel =
      MethodChannel('gamevoice/native_live_audio_capture');
  static const EventChannel _eventChannel =
      EventChannel('gamevoice/native_live_audio_capture/stream');
  int _sessionCounter = 0;

  @override
  bool get isSupported => Platform.isAndroid;

  @override
  Future<Stream<List<int>>> startLiveCapture() async {
    if (!isSupported) {
      throw UnsupportedError(
          'Native live audio capture is only supported on Android.');
    }
    debugPrint('[LIVE][bridge] starting native capture bridge');
    MobileDiagnostics.record(
        component: 'bridge', event: 'native_start_invoked');
    final sessionId =
        'native-${DateTime.now().toUtc().microsecondsSinceEpoch}-${_sessionCounter++}';
    late StreamSubscription<dynamic> nativeSubscription;
    final controller = StreamController<List<int>>();
    controller.onListen = () {
      nativeSubscription = _eventChannel
          .receiveBroadcastStream({'session_id': sessionId}).listen(
        (event) {
          controller.add(_decodeChunk(event));
        },
        onError: controller.addError,
        onDone: controller.close,
      );
      unawaited(_methodChannel.invokeMethod<void>(
        'start',
        {'session_id': sessionId},
      ));
    };
    controller.onCancel = () async {
      await nativeSubscription.cancel();
    };
    final stream = controller.stream.asBroadcastStream();
    debugPrint('[LIVE][bridge] native capture stream ready');
    MobileDiagnostics.record(component: 'bridge', event: 'native_stream_ready');
    return stream;
  }

  @override
  Future<void> stopLiveCapture() async {
    if (!isSupported) {
      return;
    }
    try {
      debugPrint('[LIVE][bridge] stopping native capture bridge');
      MobileDiagnostics.record(
          component: 'bridge', event: 'native_stop_invoked');
      await _methodChannel.invokeMethod<void>('stop');
    } on MissingPluginException {
      // Allow tests or partial platform setups to degrade cleanly.
    }
  }

  List<int> _decodeChunk(dynamic event) {
    if (event is Uint8List) {
      return event.toList();
    }
    if (event is List<int>) {
      return event;
    }
    if (event is List) {
      return event.cast<int>();
    }
    throw const FormatException('Unsupported native live audio chunk type');
  }

  Future<void> _handleNativeMethodCall(MethodCall call) async {
    if (call.method != 'diagnostic') {
      return;
    }
    final args = call.arguments;
    if (args is! Map) {
      return;
    }
    MobileDiagnostics.record(
      component: 'native_capture',
      event: args['event'] as String? ?? 'unknown',
      details: Map<String, Object?>.from(args['details'] as Map? ?? const {}),
    );
  }
}
