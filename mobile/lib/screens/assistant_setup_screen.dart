import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../backend/gamevoice_repository.dart';
import '../models/assistant_config.dart';
import '../data/personality_templates.dart';
import '../data/voice_ids.dart';
import '../services/assistant_config_storage.dart';
import '../services/voice_preview_player.dart';

class AssistantSetupScreen extends StatefulWidget {
  const AssistantSetupScreen({
    super.key,
    required this.repository,
  });

  final GameVoiceRepository repository;

  @override
  State<AssistantSetupScreen> createState() => _AssistantSetupScreenState();
}

class _AssistantSetupScreenState extends State<AssistantSetupScreen> {
  late TextEditingController _nameController;
  late TextEditingController _descriptionController;
  PersonalityTemplate _selectedTemplate = personalityTemplates[0];
  VoiceIdOption _selectedVoiceId = voiceIdOptions[0];
  AssistantConfigStorage? _storage;
  late VoicePreviewPlayer _voicePlayer;

  @override
  void initState() {
    super.initState();
    _nameController = TextEditingController(text: '宝子');
    _descriptionController = TextEditingController(text: _selectedTemplate.description);
    _voicePlayer = VoicePreviewPlayer(
      baseUri: widget.repository.latestTtsAudioUri(tableId: 'x').replace(path: ''),
    );
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    final prefs = await SharedPreferences.getInstance();
    _storage = AssistantConfigStorage(prefs);
    final config = await _storage!.load();
    if (mounted) {
      setState(() {
        _nameController.text = config.name;
        _selectedTemplate = config.personalityTemplate;
        _descriptionController.text = config.personalityDescription;
        _selectedVoiceId = config.voiceId;
      });
    }
  }

  Future<void> _saveConfig() async {
    if (_storage == null) return;
    final config = AssistantConfig(
      name: _nameController.text.trim().isEmpty ? '宝子' : _nameController.text.trim(),
      personalityTemplate: _selectedTemplate,
      personalityDescription: _descriptionController.text,
      voiceId: _selectedVoiceId,
    );
    await _storage!.save(config);
  }

  void _onTemplateChanged(PersonalityTemplate template) {
    setState(() {
      _selectedTemplate = template;
      _descriptionController.text = template.description;
    });
    _saveConfig();
  }

  void _playPreview() {
    if (_selectedVoiceId.filename.isEmpty) {
      return;
    }
    _voicePlayer.play(_selectedVoiceId.filename);
  }

  @override
  void dispose() {
    _nameController.dispose();
    _descriptionController.dispose();
    _voicePlayer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('设定助手'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('助手名称', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _nameController,
            decoration: const InputDecoration(
              hintText: '宝子',
              border: OutlineInputBorder(),
            ),
            onChanged: (_) => _saveConfig(),
          ),
          const SizedBox(height: 24),
          Text('人格模板', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          ...personalityTemplates.map((template) => RadioListTile<PersonalityTemplate>(
                title: Text(template.name),
                subtitle: Text(template.description),
                value: template,
                groupValue: _selectedTemplate,
                onChanged: (v) => _onTemplateChanged(v!),
              )),
          const SizedBox(height: 24),
          Text('人设描述', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _descriptionController,
            maxLines: 5,
            decoration: const InputDecoration(
              hintText: '描述助手的人设...',
              border: OutlineInputBorder(),
            ),
            onChanged: (_) => _saveConfig(),
          ),
          const SizedBox(height: 24),
          Text('Voice ID', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          DropdownButtonFormField<VoiceIdOption>(
            value: _selectedVoiceId,
            decoration: const InputDecoration(border: OutlineInputBorder()),
            items: voiceIdOptions.map((option) {
              final suffix = option.voiceId.isEmpty ? '' : ' (${option.voiceId})';
              return DropdownMenuItem(
                value: option,
                child: Text('${option.label}$suffix'),
              );
            }).toList(),
            onChanged: (v) {
              if (v != null) {
                setState(() => _selectedVoiceId = v);
                _saveConfig();
              }
            },
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _selectedVoiceId.filename.isEmpty ? null : _playPreview,
            icon: const Icon(Icons.volume_up),
            label: Text('试听: ${_selectedVoiceId.label}'),
          ),
        ],
      ),
    );
  }
}
