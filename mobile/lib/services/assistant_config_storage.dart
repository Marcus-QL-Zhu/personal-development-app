import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/assistant_config.dart';

class AssistantConfigStorage {
  static const _key = 'assistant_config';

  final SharedPreferences _prefs;

  AssistantConfigStorage(this._prefs);

  Future<AssistantConfig> load() async {
    final jsonStr = _prefs.getString(_key);
    if (jsonStr == null) {
      return AssistantConfig.defaultConfig;
    }
    try {
      final json = jsonDecode(jsonStr) as Map<String, dynamic>;
      return AssistantConfig.fromJson(json);
    } catch (_) {
      return AssistantConfig.defaultConfig;
    }
  }

  Future<void> save(AssistantConfig config) async {
    final jsonStr = jsonEncode(config.toJson());
    await _prefs.setString(_key, jsonStr);
  }

  Future<void> clear() async {
    await _prefs.remove(_key);
  }
}