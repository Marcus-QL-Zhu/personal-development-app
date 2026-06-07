import 'dart:io';

import 'package:flutter/services.dart';

abstract class DuplexAudioSession {
  Future<void> activate();

  Future<void> deactivate();
}

class PlatformDuplexAudioSession implements DuplexAudioSession {
  static const MethodChannel _channel =
      MethodChannel('gamevoice/duplex_audio_session');

  bool _active = false;

  @override
  Future<void> activate() async {
    if (_active) {
      return;
    }
    if (!Platform.isAndroid) {
      _active = true;
      return;
    }
    try {
      await _channel.invokeMethod<void>('activate');
      _active = true;
    } on MissingPluginException {
      _active = true;
    }
  }

  @override
  Future<void> deactivate() async {
    if (!_active) {
      return;
    }
    if (!Platform.isAndroid) {
      _active = false;
      return;
    }
    try {
      await _channel.invokeMethod<void>('deactivate');
    } on MissingPluginException {
      // Allow widget tests and unsupported platforms to no-op cleanly.
    } finally {
      _active = false;
    }
  }
}
