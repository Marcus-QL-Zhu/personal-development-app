import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/assistant_config.dart';
import '../services/assistant_config_storage.dart';
import '../backend/gamevoice_repository.dart';
import 'table_shell_screen.dart';

class OpenTableScreen extends StatefulWidget {
  const OpenTableScreen({
    super.key,
    required this.repository,
  });

  final GameVoiceRepository repository;

  @override
  State<OpenTableScreen> createState() => _OpenTableScreenState();
}

class _OpenTableScreenState extends State<OpenTableScreen> {
  AssistantConfig? _config;
  bool _isLoading = false;
  late TextEditingController _tableNameController;
  int _nextTableNumber = 1;

  static const _lastTableNumberKey = 'last_table_number';

  @override
  void initState() {
    super.initState();
    _tableNameController = TextEditingController();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    final prefs = await SharedPreferences.getInstance();
    final storage = AssistantConfigStorage(prefs);
    final config = await storage.load();
    final lastNumber = prefs.getInt(_lastTableNumberKey) ?? 0;
    if (mounted) {
      setState(() {
        _config = config;
        _nextTableNumber = lastNumber + 1;
        _tableNameController.text = 'Table $_nextTableNumber';
      });
    }
  }

  Future<void> _openTable() async {
    if (_config == null) return;
    final tableName = _tableNameController.text.trim().isEmpty
        ? 'Table $_nextTableNumber'
        : _tableNameController.text.trim();
    setState(() => _isLoading = true);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(_lastTableNumberKey, _nextTableNumber);

      final table = await widget.repository.createTable(
        tableName,
        assistantName: _config!.name,
        assistantPersonality: _config!.personalityDescription,
        assistantVoiceId: _config!.voiceId.voiceId,
      );
      if (mounted) {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(
            builder: (_) => TableShellScreen(
              table: table,
              repository: widget.repository,
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('开桌失败: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  void dispose() {
    _tableNameController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final config = _config;
    return Scaffold(
      appBar: AppBar(title: const Text('开桌')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('当前助手配置', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 12),
                    Text('名称: ${config?.name ?? '加载中...'}'),
                    const SizedBox(height: 4),
                    Text('人格: ${config?.personalityTemplate.name ?? '加载中...'}'),
                    const SizedBox(height: 4),
                    Text('描述: ${config?.personalityDescription ?? '加载中...'}'),
                    const SizedBox(height: 4),
                    Text('Voice: ${config?.voiceId.label ?? '加载中...'}'),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _tableNameController,
              decoration: const InputDecoration(
                labelText: '桌名',
                border: OutlineInputBorder(),
                hintText: '输入桌名或使用默认',
              ),
            ),
            const Spacer(),
            FilledButton(
              onPressed: _isLoading ? null : _openTable,
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 20),
              ),
              child: _isLoading
                  ? const CircularProgressIndicator()
                  : const Text('确认开桌', style: TextStyle(fontSize: 18)),
            ),
          ],
        ),
      ),
    );
  }
}
