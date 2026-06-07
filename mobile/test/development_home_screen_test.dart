import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/audio/voice_recorder.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/development/development_home_screen.dart';
import 'package:personal_development_app/development/development_repository.dart';

void main() {
  testWidgets('creates employee and uploads confirmed coach recording',
      (tester) async {
    final repository = _FakeDevelopmentRepository();
    final recorder = _FakeVoiceRecorder();

    await tester.pumpWidget(
      MaterialApp(
        home: DevelopmentHomeScreen(
          repository: repository,
          voiceRecorderFactory: () => recorder,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.widgetWithText(TextField, '姓名'), 'Alice');
    await tester.enterText(find.widgetWithText(TextField, 'Gallup 34 排序'), '1 Learner\n2 Strategic');
    await tester.enterText(find.widgetWithText(TextField, '自然语言介绍'), 'New consultant.');
    await tester.tap(find.text('保存员工档案'));
    await tester.pumpAndSettle();

    expect(repository.employees.single.name, 'Alice');
    expect(find.text('Alice'), findsWidgets);

    await tester.tap(find.text('开始录音'));
    await tester.pumpAndSettle();
    expect(recorder.started, isTrue);

    await tester.tap(find.text('停止录音'));
    await tester.pumpAndSettle();
    expect(find.text('上传这段录音?'), findsOneWidget);

    await tester.tap(find.text('上传'));
    await tester.pumpAndSettle();

    expect(repository.uploadedClips.single.filename, 'coach.wav');
    await tester.drag(find.byType(ListView), const Offset(0, -500));
    await tester.pumpAndSettle();
    expect(find.textContaining('知识点'), findsOneWidget);
    expect(find.textContaining('Manager Feedback'), findsOneWidget);
  });
}

class _FakeVoiceRecorder implements VoiceRecorder {
  bool started = false;

  @override
  Future<void> dispose() async {}

  @override
  Future<bool> ensurePermission() async => true;

  @override
  Future<void> start() async {
    started = true;
  }

  @override
  Future<UploadFilePayload?> stop() async {
    return const UploadFilePayload(filename: 'coach.wav', bytes: [1, 2, 3]);
  }

  @override
  Future<Stream<List<int>>> startLiveStream() {
    throw UnimplementedError();
  }

  @override
  Future<void> stopLiveStream() async {}
}

class _FakeDevelopmentRepository implements DevelopmentRepository {
  final employees = <DevelopmentEmployee>[];
  final sessions = <DevelopmentCoachingSession>[];
  final uploadedClips = <UploadFilePayload>[];

  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<List<DevelopmentEmployee>> listEmployees() async => employees;

  @override
  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final employee = DevelopmentEmployee(
      id: 'emp-1',
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
      gallupStrengths: const [
        GallupStrength(rank: 1, name: 'Learner'),
        GallupStrength(rank: 2, name: 'Strategic'),
      ],
    );
    employees.add(employee);
    return employee;
  }

  @override
  Future<DevelopmentEmployee> updateEmployee({
    required String employeeId,
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final employee = employees.first;
    final updated = employee.copyWith(
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
    );
    employees[0] = updated;
    return updated;
  }

  @override
  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  }) async =>
      sessions;

  @override
  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    uploadedClips.add(clip);
    final session = DevelopmentCoachingSession(
      id: 'session-1',
      employeeId: employeeId,
      coachDate: '2026-06-07',
      topic: '客户需求澄清',
      contentSummary: '知识点：先复述客户目标。',
      actionPlan: '本次未形成明确 Action Plan。',
      managerFeedback: '讲得不错。Manager Feedback: 增加复述练习。',
      transcriptText: 'manager: ...',
      qualityStatus: 'ok',
      syncStatus: 'synced',
    );
    sessions.insert(0, session);
    return session;
  }
}
