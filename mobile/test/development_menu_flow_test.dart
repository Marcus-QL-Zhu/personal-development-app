import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/audio/voice_recorder.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/development/development_main_menu_screen.dart';
import 'package:personal_development_app/development/development_repository.dart';

void main() {
  testWidgets('main menu exposes personal development actions', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: DevelopmentMainMenuScreen(repository: _FakeDevelopmentRepository()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('新增顾问'), findsOneWidget);
    expect(find.text('编辑履历'), findsOneWidget);
    expect(find.text('coach历史'), findsOneWidget);
    expect(find.text('调试功能'), findsOneWidget);
    expect(find.text('设定助手'), findsNothing);
    expect(find.text('开桌'), findsNothing);
    expect(find.text('加载历史'), findsNothing);
  });

  testWidgets('creates consultant with name only and shows it in edit and history pickers',
      (tester) async {
    final repository = _FakeDevelopmentRepository();
    await tester.pumpWidget(MaterialApp(home: DevelopmentMainMenuScreen(repository: repository)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('新增顾问'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, '名称'), 'Alice');
    await tester.tap(find.text('确认保存'));
    await tester.pumpAndSettle();

    expect(repository.employees.single.name, 'Alice');
    expect(repository.employees.single.profileNote, '');
    expect(repository.employees.single.gallupRaw, '');

    await tester.tap(find.text('编辑履历'));
    await tester.pumpAndSettle();
    expect(find.text('Alice'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();

    await tester.tap(find.text('coach历史'));
    await tester.pumpAndSettle();
    expect(find.text('Alice'), findsOneWidget);
  });

  testWidgets('edit profile saves changed consultant information', (tester) async {
    final repository = _FakeDevelopmentRepository()
      ..employees.add(const DevelopmentEmployee(
        id: 'employee-1',
        name: 'Alice',
        gallupRaw: '',
        profileNote: '',
        gallupStrengths: [],
      ));
    await tester.pumpWidget(MaterialApp(home: DevelopmentMainMenuScreen(repository: repository)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('编辑履历'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Alice'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, '名称'), 'Alice Zhang');
    await tester.enterText(find.widgetWithText(TextField, '介绍'), 'New profile note');
    await tester.tap(find.text('确认保存'));
    await tester.pumpAndSettle();

    expect(repository.employees.single.name, 'Alice Zhang');
    expect(repository.employees.single.profileNote, 'New profile note');
  });

  testWidgets('coach history is sorted newest first and upload is confirmed after recording',
      (tester) async {
    final repository = _FakeDevelopmentRepository();
    final employee = const DevelopmentEmployee(
      id: 'employee-1',
      name: 'Alice',
      gallupRaw: '',
      profileNote: '',
      gallupStrengths: [],
    );
    repository.employees.add(employee);
    repository.sessions[employee.id] = [
      const DevelopmentCoachingSession(
        id: 'old',
        employeeId: 'employee-1',
        coachDate: '2026-06-01',
        topic: '旧记录',
        contentSummary: '旧总结',
        actionPlan: '旧行动',
        managerFeedback: '旧 notes',
        transcriptText: '旧转写',
        qualityStatus: 'ok',
        syncStatus: 'synced',
      ),
      const DevelopmentCoachingSession(
        id: 'new',
        employeeId: 'employee-1',
        coachDate: '2026-06-07',
        topic: '新记录',
        contentSummary: '新总结',
        actionPlan: '新行动',
        managerFeedback: '新 notes',
        transcriptText: '新转写',
        qualityStatus: 'ok',
        syncStatus: 'synced',
      ),
    ];
    final recorder = _FakeVoiceRecorder();

    await tester.pumpWidget(MaterialApp(
      home: DevelopmentMainMenuScreen(
        repository: repository,
        voiceRecorderFactory: () => recorder,
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('coach历史'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Alice'));
    await tester.pumpAndSettle();

    final newTop = tester.getTopLeft(find.textContaining('新记录')).dy;
    final oldTop = tester.getTopLeft(find.textContaining('旧记录')).dy;
    expect(newTop, lessThan(oldTop));
    expect(find.byType(Scrollable), findsWidgets);

    await tester.tap(find.text('开始录音'));
    await tester.pump();
    expect(find.text('正在录音'), findsOneWidget);
    expect(find.text('结束录音'), findsOneWidget);

    await tester.tap(find.text('结束录音'));
    await tester.pumpAndSettle();
    expect(find.text('上传这段录音?'), findsOneWidget);

    await tester.tap(find.text('上传'));
    await tester.pumpAndSettle();
    expect(repository.uploadedClips.single.filename, 'coach.wav');
  });

  testWidgets('coach detail shows summary action plan manager notes and collapsed transcript',
      (tester) async {
    final repository = _FakeDevelopmentRepository();
    final employee = const DevelopmentEmployee(
      id: 'employee-1',
      name: 'Alice',
      gallupRaw: '',
      profileNote: '',
      gallupStrengths: [],
    );
    repository.employees.add(employee);
    repository.sessions[employee.id] = [
      const DevelopmentCoachingSession(
        id: 'session-1',
        employeeId: 'employee-1',
        coachDate: '2026-06-07',
        topic: '客户需求澄清',
        contentSummary: '知识点：先复述客户目标。',
        actionPlan: '下次先写三句复述。',
        managerFeedback: '讲得清楚，但可以增加员工复述练习。',
        transcriptText: '完整转写内容',
        qualityStatus: 'ok',
        syncStatus: 'synced',
      ),
    ];

    await tester.pumpWidget(MaterialApp(home: DevelopmentMainMenuScreen(repository: repository)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('coach历史'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Alice'));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('客户需求澄清'));
    await tester.pumpAndSettle();

    expect(find.text('内容总结'), findsOneWidget);
    expect(find.text('知识点：先复述客户目标。'), findsOneWidget);
    expect(find.text('Action Plan'), findsOneWidget);
    expect(find.text('下次先写三句复述。'), findsOneWidget);
    expect(find.text('Manager Notes'), findsOneWidget);
    expect(find.text('讲得清楚，但可以增加员工复述练习。'), findsOneWidget);
    expect(find.text('完整转写内容'), findsNothing);

    await tester.tap(find.text('查看完整转写'));
    await tester.pumpAndSettle();
    expect(find.text('完整转写内容'), findsOneWidget);
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
    started = false;
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
  final sessions = <String, List<DevelopmentCoachingSession>>{};
  final uploadedClips = <UploadFilePayload>[];

  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<List<DevelopmentEmployee>> listEmployees() async => List.unmodifiable(employees);

  @override
  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final employee = DevelopmentEmployee(
      id: 'employee-${employees.length + 1}',
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
      gallupStrengths: const [],
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
    final index = employees.indexWhere((item) => item.id == employeeId);
    final updated = DevelopmentEmployee(
      id: employeeId,
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
      gallupStrengths: const [],
    );
    employees[index] = updated;
    return updated;
  }

  @override
  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  }) async {
    return List.unmodifiable(sessions[employeeId] ?? const []);
  }

  @override
  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    uploadedClips.add(clip);
    final session = DevelopmentCoachingSession(
      id: 'session-${(sessions[employeeId] ?? const []).length + 1}',
      employeeId: employeeId,
      coachDate: '2026-06-08',
      topic: '上传后的 coach',
      contentSummary: '内容总结',
      actionPlan: '本次未形成明确 Action Plan。',
      managerFeedback: 'Manager notes',
      transcriptText: '完整转写',
      qualityStatus: 'ok',
      syncStatus: 'synced',
    );
    sessions.putIfAbsent(employeeId, () => []).add(session);
    return session;
  }
}
