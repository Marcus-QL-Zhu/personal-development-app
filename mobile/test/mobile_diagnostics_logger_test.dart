import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/diagnostics/mobile_diagnostics_logger.dart';

class _RecordingRepository implements GameVoiceRepository {
  final uploaded = <MobileDiagnosticEntry>[];

  @override
  Future<void> uploadMobileDiagnostics({
    required String tableId,
    required List<MobileDiagnosticEntry> entries,
  }) async {
    uploaded.addAll(entries);
  }

  @override
  noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  test('mobile diagnostics logger buffers entries and flushes them', () async {
    final repository = _RecordingRepository();
    final logger = MobileDiagnosticsLogger(
      tableId: 'table-1',
      repository: repository,
      sessionId: 'session-1',
      now: () => DateTime.utc(2026, 5, 10, 10, 40),
    );

    logger.record(
      component: 'table_shell',
      event: 'live_start_requested',
      details: const {'route': 'table_shell'},
    );

    expect(logger.snapshot(), hasLength(1));

    await logger.flush();

    expect(repository.uploaded, hasLength(1));
    expect(repository.uploaded.single.sessionId, 'session-1');
    expect(repository.uploaded.single.component, 'table_shell');
    expect(repository.uploaded.single.event, 'live_start_requested');
    expect(logger.snapshot(), isEmpty);
  });
}
