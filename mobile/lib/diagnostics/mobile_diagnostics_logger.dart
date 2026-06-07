import 'dart:async';

import 'package:flutter/foundation.dart';

import '../backend/gamevoice_repository.dart';

typedef DiagnosticsClock = DateTime Function();

class MobileDiagnostics {
  static MobileDiagnosticsLogger? active;

  static void record({
    required String component,
    required String event,
    Map<String, Object?> details = const {},
  }) {
    active?.record(component: component, event: event, details: details);
  }

  static Future<void> flush() async {
    await active?.flush();
  }
}

class MobileDiagnosticsLogger {
  MobileDiagnosticsLogger({
    required this.tableId,
    required this.repository,
    required this.sessionId,
    DiagnosticsClock? now,
    this.maxEntries = 300,
  }) : _now = now ?? DateTime.now;

  final String tableId;
  final GameVoiceRepository repository;
  final String sessionId;
  final int maxEntries;
  final DiagnosticsClock _now;
  final List<MobileDiagnosticEntry> _entries = [];
  bool _flushInFlight = false;

  void record({
    required String component,
    required String event,
    Map<String, Object?> details = const {},
  }) {
    final entry = MobileDiagnosticEntry(
      ts: _now().toUtc().toIso8601String(),
      sessionId: sessionId,
      component: component,
      event: event,
      details: details,
    );
    _entries.add(entry);
    final overflow = _entries.length - maxEntries;
    if (overflow > 0) {
      _entries.removeRange(0, overflow);
    }
    debugPrint('[MOBILE_DIAG][$component] $event $details');
  }

  List<MobileDiagnosticEntry> snapshot() {
    return List<MobileDiagnosticEntry>.unmodifiable(_entries);
  }

  Future<void> flush() async {
    if (_flushInFlight || _entries.isEmpty) {
      return;
    }
    _flushInFlight = true;
    final batch = List<MobileDiagnosticEntry>.from(_entries);
    try {
      await repository.uploadMobileDiagnostics(
        tableId: tableId,
        entries: batch,
      );
      _entries.removeRange(0, batch.length.clamp(0, _entries.length));
    } catch (error) {
      debugPrint('[MOBILE_DIAG][upload] failed $error');
    } finally {
      _flushInFlight = false;
    }
  }
}
