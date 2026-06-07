import '../backend/gamevoice_repository.dart';

class GallupStrength {
  const GallupStrength({required this.rank, required this.name});

  factory GallupStrength.fromJson(Map<String, dynamic> json) {
    return GallupStrength(
      rank: json['rank'] as int? ?? 0,
      name: json['name'] as String? ?? '',
    );
  }

  final int rank;
  final String name;
}

class DevelopmentEmployee {
  const DevelopmentEmployee({
    required this.id,
    required this.name,
    required this.gallupRaw,
    required this.profileNote,
    this.gallupStrengths = const [],
  });

  factory DevelopmentEmployee.fromJson(Map<String, dynamic> json) {
    final strengths = json['gallup_strengths'] as List<dynamic>? ?? const [];
    return DevelopmentEmployee(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      gallupRaw: json['gallup_raw'] as String? ?? '',
      profileNote: json['profile_note'] as String? ?? '',
      gallupStrengths: strengths
          .whereType<Map<String, dynamic>>()
          .map(GallupStrength.fromJson)
          .toList(),
    );
  }

  final String id;
  final String name;
  final String gallupRaw;
  final String profileNote;
  final List<GallupStrength> gallupStrengths;

  DevelopmentEmployee copyWith({
    String? name,
    String? gallupRaw,
    String? profileNote,
    List<GallupStrength>? gallupStrengths,
  }) {
    return DevelopmentEmployee(
      id: id,
      name: name ?? this.name,
      gallupRaw: gallupRaw ?? this.gallupRaw,
      profileNote: profileNote ?? this.profileNote,
      gallupStrengths: gallupStrengths ?? this.gallupStrengths,
    );
  }
}

class DevelopmentCoachingSession {
  const DevelopmentCoachingSession({
    required this.id,
    required this.employeeId,
    required this.coachDate,
    required this.topic,
    required this.contentSummary,
    required this.actionPlan,
    required this.managerFeedback,
    required this.transcriptText,
    required this.qualityStatus,
    required this.syncStatus,
  });

  factory DevelopmentCoachingSession.fromJson(Map<String, dynamic> json) {
    return DevelopmentCoachingSession(
      id: json['id'] as String? ?? '',
      employeeId: json['employee_id'] as String? ?? '',
      coachDate: json['coach_date'] as String? ?? '',
      topic: json['topic'] as String? ?? '',
      contentSummary: json['content_summary'] as String? ?? '',
      actionPlan: json['action_plan'] as String? ?? '',
      managerFeedback: json['manager_feedback'] as String? ?? '',
      transcriptText: json['transcript_text'] as String? ?? '',
      qualityStatus: json['quality_status'] as String? ?? '',
      syncStatus: json['sync_status'] as String? ?? '',
    );
  }

  final String id;
  final String employeeId;
  final String coachDate;
  final String topic;
  final String contentSummary;
  final String actionPlan;
  final String managerFeedback;
  final String transcriptText;
  final String qualityStatus;
  final String syncStatus;
}

abstract class DevelopmentRepository {
  Future<bool> healthCheck();

  Future<List<DevelopmentEmployee>> listEmployees();

  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  });

  Future<DevelopmentEmployee> updateEmployee({
    required String employeeId,
    required String name,
    required String gallupRaw,
    required String profileNote,
  });

  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  });

  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  });
}
