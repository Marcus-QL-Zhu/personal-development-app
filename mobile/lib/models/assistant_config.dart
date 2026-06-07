import '../data/personality_templates.dart';
import '../data/voice_ids.dart';

class AssistantConfig {
  final String name;
  final PersonalityTemplate personalityTemplate;
  final String personalityDescription;
  final VoiceIdOption voiceId;

  const AssistantConfig({
    required this.name,
    required this.personalityTemplate,
    required this.personalityDescription,
    required this.voiceId,
  });

  static AssistantConfig get defaultConfig => AssistantConfig(
        name: '宝子',
        personalityTemplate: defaultPersonalityTemplate,
        personalityDescription: defaultPersonalityTemplate.description,
        voiceId: defaultVoiceId,
      );

  AssistantConfig copyWith({
    String? name,
    PersonalityTemplate? personalityTemplate,
    String? personalityDescription,
    VoiceIdOption? voiceId,
  }) {
    return AssistantConfig(
      name: name ?? this.name,
      personalityTemplate: personalityTemplate ?? this.personalityTemplate,
      personalityDescription: personalityDescription ?? this.personalityDescription,
      voiceId: voiceId ?? this.voiceId,
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'personalityTemplateId': personalityTemplate.id,
        'personalityDescription': personalityDescription,
        'voiceId': voiceId.voiceId,
      };

  factory AssistantConfig.fromJson(Map<String, dynamic> json) {
    final templateId = json['personalityTemplateId'] as String;
    final template = personalityTemplates.firstWhere(
      (t) => t.id == templateId,
      orElse: () => defaultPersonalityTemplate,
    );
    final voiceIdStr = json['voiceId'] as String;
    final voice = voiceIdOptions.firstWhere(
      (v) => v.voiceId == voiceIdStr,
      orElse: () => defaultVoiceId,
    );
    return AssistantConfig(
      name: json['name'] as String,
      personalityTemplate: template,
      personalityDescription: json['personalityDescription'] as String,
      voiceId: voice,
    );
  }
}
