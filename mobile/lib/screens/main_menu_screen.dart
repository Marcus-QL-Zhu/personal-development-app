import 'package:flutter/material.dart';
import '../backend/gamevoice_repository.dart';
import 'assistant_setup_screen.dart';
import 'open_table_screen.dart';
import 'load_history_screen.dart';
import 'debug_functions_screen.dart';

class MainMenuScreen extends StatefulWidget {
  const MainMenuScreen({
    super.key,
    required this.repository,
    required this.onBackendUrlChanged,
  });

  final GameVoiceRepository repository;
  final ValueChanged<String> onBackendUrlChanged;

  @override
  State<MainMenuScreen> createState() => _MainMenuScreenState();
}

class _MainMenuScreenState extends State<MainMenuScreen> {
  bool _backendOnline = true;
  bool _checked = false;

  String get _backendUrl {
    final uri = widget.repository.latestTtsAudioUri(tableId: 'x');
    return '${uri.scheme}://${uri.host}:${uri.port}';
  }

  @override
  void initState() {
    super.initState();
    _checkBackend();
  }

  Future<void> _checkBackend() async {
    try {
      final ok = await widget.repository.healthCheck().timeout(
            const Duration(seconds: 5),
          );
      if (mounted) setState(() => _backendOnline = ok);
    } catch (_) {
      if (mounted) setState(() => _backendOnline = false);
    } finally {
      if (mounted) setState(() => _checked = true);
    }
  }

  Future<void> _showUrlEditDialog() async {
    final controller = TextEditingController(text: _backendUrl);
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('修改后端地址'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            hintText: 'http://192.168.1.100:8010',
            border: OutlineInputBorder(),
          ),
          keyboardType: TextInputType.url,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
    if (result != null && result.isNotEmpty) {
      widget.onBackendUrlChanged(result);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    'GameVoice',
                    style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                        ),
                  ),
                  const SizedBox(width: 12),
                  _BackendIndicator(checked: _checked, online: _backendOnline),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                '桌游陪玩助手',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
              ),
              const SizedBox(height: 32),
              _MenuButton(
                label: '设定助手',
                icon: Icons.person_outline,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) =>
                          AssistantSetupScreen(repository: widget.repository)),
                ),
              ),
              const SizedBox(height: 16),
              _MenuButton(
                label: '开桌',
                icon: Icons.play_circle_outline,
                subtitle: _checked && !_backendOnline ? '后端离线' : null,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) =>
                          OpenTableScreen(repository: widget.repository)),
                ),
              ),
              const SizedBox(height: 16),
              _MenuButton(
                label: '加载历史',
                icon: Icons.history,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) =>
                          LoadHistoryScreen(repository: widget.repository)),
                ),
              ),
              const SizedBox(height: 16),
              _MenuButton(
                label: '调试功能',
                icon: Icons.bug_report_outlined,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) => DebugFunctionsScreen(
                            repository: widget.repository,
                            onBackendUrlChanged: widget.onBackendUrlChanged,
                          )),
                ).then((result) {
                  if (result == 'url_changed') {
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                          builder: (_) => DebugFunctionsScreen(
                                repository: widget.repository,
                                onBackendUrlChanged: widget.onBackendUrlChanged,
                              )),
                    );
                  }
                }),
              ),
              const Spacer(),
              if (_checked && !_backendOnline)
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.red.shade50,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.red.shade200),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.cloud_off,
                          color: Colors.red.shade700, size: 20),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '无法连接后端服务，请检查网络',
                          style: TextStyle(
                              color: Colors.red.shade700, fontSize: 13),
                        ),
                      ),
                      TextButton(
                        onPressed: () {
                          setState(() => _checked = false);
                          _checkBackend();
                        },
                        child: const Text('重试'),
                      ),
                      TextButton(
                        onPressed: _showUrlEditDialog,
                        child: const Text('编辑后端IP'),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _BackendIndicator extends StatelessWidget {
  const _BackendIndicator({required this.checked, required this.online});

  final bool checked;
  final bool online;

  @override
  Widget build(BuildContext context) {
    if (!checked) {
      return const SizedBox(
        width: 14,
        height: 14,
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }
    return Icon(
      online ? Icons.cloud_done : Icons.cloud_off,
      size: 20,
      color: online ? Colors.green : Colors.red,
    );
  }
}

class _MenuButton extends StatelessWidget {
  const _MenuButton({
    required this.label,
    required this.icon,
    required this.onTap,
    this.subtitle,
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: 1.0,
      child: Material(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(16),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
            child: Row(
              children: [
                Icon(icon, size: 28),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        label,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      if (subtitle != null)
                        Text(
                          subtitle!,
                          style: TextStyle(
                            fontSize: 12,
                            color: Colors.red.shade700,
                          ),
                        ),
                    ],
                  ),
                ),
                Icon(
                  Icons.chevron_right,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
