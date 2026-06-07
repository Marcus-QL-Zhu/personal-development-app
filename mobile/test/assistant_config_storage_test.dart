import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:personal_development_app/services/assistant_config_storage.dart';
import 'package:personal_development_app/data/personality_templates.dart';
import 'package:personal_development_app/data/voice_ids.dart';
import 'package:personal_development_app/models/assistant_config.dart';

void main() {
  test('save and load round-trip', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final storage = AssistantConfigStorage(prefs);

    final config = AssistantConfig(
      name: '阿夏',
      personalityTemplate: personalityTemplates[1],
      personalityDescription: personalityTemplates[1].description,
      voiceId: voiceIdOptions[0],
    );

    await storage.save(config);
    final loaded = await storage.load();

    expect(loaded.name, '阿夏');
    expect(loaded.personalityTemplate.id, 'humorous');
    expect(loaded.voiceId.voiceId, '');
  });

  test('load defaults when no saved config', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final storage = AssistantConfigStorage(prefs);

    final loaded = await storage.load();
    expect(loaded.name, '宝子');
    expect(loaded.personalityTemplate.id, 'gentle');
  });
}
