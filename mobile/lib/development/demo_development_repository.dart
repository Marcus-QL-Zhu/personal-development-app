import '../backend/gamevoice_repository.dart';
import 'development_repository.dart';

class DemoDevelopmentRepository implements DevelopmentRepository {
  DemoDevelopmentRepository() {
    _employees.add(
      const DevelopmentEmployee(
        id: 'demo-employee',
        name: 'Demo Employee',
        gallupRaw: '1 Learner\n2 Strategic\n3 Achiever',
        profileNote: 'Demo profile for local browser testing.',
        gallupStrengths: [
          GallupStrength(rank: 1, name: 'Learner'),
          GallupStrength(rank: 2, name: 'Strategic'),
          GallupStrength(rank: 3, name: 'Achiever'),
        ],
      ),
    );
  }

  final List<DevelopmentEmployee> _employees = [];
  final List<DevelopmentCoachingSession> _sessions = [];

  @override
  Future<bool> healthCheck() async => true;

  @override
  Future<List<DevelopmentEmployee>> listEmployees() async => List.unmodifiable(_employees);

  @override
  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final employee = DevelopmentEmployee(
      id: 'demo-${_employees.length + 1}',
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
      gallupStrengths: _parseDemoGallup(gallupRaw),
    );
    _employees.insert(0, employee);
    return employee;
  }

  @override
  Future<DevelopmentEmployee> updateEmployee({
    required String employeeId,
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final index = _employees.indexWhere((item) => item.id == employeeId);
    final updated = DevelopmentEmployee(
      id: employeeId,
      name: name,
      gallupRaw: gallupRaw,
      profileNote: profileNote,
      gallupStrengths: _parseDemoGallup(gallupRaw),
    );
    if (index >= 0) {
      _employees[index] = updated;
    } else {
      _employees.insert(0, updated);
    }
    return updated;
  }

  @override
  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  }) async {
    return _sessions.where((item) => item.employeeId == employeeId).toList();
  }

  @override
  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    final session = DevelopmentCoachingSession(
      id: 'demo-session-${_sessions.length + 1}',
      employeeId: employeeId,
      coachDate: DateTime.now().toIso8601String().substring(0, 10),
      topic: 'Demo Coach Summary',
      contentSummary: '知识点：这是浏览器 demo 生成的内容总结，真实模式会调用后端 ASR 和 MiniMax M3。',
      actionPlan: '本次未形成明确 Action Plan。',
      managerFeedback: 'Demo manager feedback：真实模式会结合 Gallup 和语音内容生成。',
      transcriptText: 'Demo transcript from ${clip.filename}.',
      qualityStatus: 'demo',
      syncStatus: 'demo',
    );
    _sessions.insert(0, session);
    return session;
  }
}

List<GallupStrength> _parseDemoGallup(String raw) {
  final strengths = <GallupStrength>[];
  for (final line in raw.split('\n')) {
    final match = RegExp(r'^\s*(\d{1,2})\s*[\.\)）:：、-]?\s*(.+?)\s*$').firstMatch(line);
    if (match == null) continue;
    strengths.add(
      GallupStrength(
        rank: int.tryParse(match.group(1) ?? '') ?? 0,
        name: match.group(2)?.trim() ?? '',
      ),
    );
  }
  strengths.sort((a, b) => a.rank.compareTo(b.rank));
  return strengths;
}
