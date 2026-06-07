import 'package:flutter/material.dart';

import '../audio/voice_recorder.dart';
import 'development_consultant_picker_screen.dart';
import 'development_profile_form_screen.dart';
import 'development_repository.dart';

class DevelopmentMainMenuScreen extends StatefulWidget {
  const DevelopmentMainMenuScreen({
    super.key,
    required this.repository,
    this.voiceRecorderFactory,
    this.debugBuilder,
  });

  final DevelopmentRepository repository;
  final VoiceRecorder Function()? voiceRecorderFactory;
  final WidgetBuilder? debugBuilder;

  @override
  State<DevelopmentMainMenuScreen> createState() =>
      _DevelopmentMainMenuScreenState();
}

class _DevelopmentMainMenuScreenState extends State<DevelopmentMainMenuScreen> {
  bool _backendOnline = true;
  bool _checked = false;

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

  void _open(Widget screen) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => screen));
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
                    'Personal Development',
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                          fontWeight: FontWeight.bold,
                        ),
                  ),
                  const SizedBox(width: 10),
                  _BackendIndicator(checked: _checked, online: _backendOnline),
                ],
              ),
              const SizedBox(height: 32),
              _MenuButton(
                label: '新增顾问',
                icon: Icons.person_add_alt_1_outlined,
                onTap: () => _open(
                  DevelopmentProfileFormScreen(repository: widget.repository),
                ),
              ),
              const SizedBox(height: 12),
              _MenuButton(
                label: '编辑履历',
                icon: Icons.edit_note,
                onTap: () => _open(
                  DevelopmentConsultantPickerScreen(
                    repository: widget.repository,
                    mode: DevelopmentConsultantPickerMode.edit,
                  ),
                ),
              ),
              const SizedBox(height: 12),
              _MenuButton(
                label: 'coach历史',
                icon: Icons.history,
                onTap: () => _open(
                  DevelopmentConsultantPickerScreen(
                    repository: widget.repository,
                    mode: DevelopmentConsultantPickerMode.history,
                    voiceRecorderFactory: widget.voiceRecorderFactory,
                  ),
                ),
              ),
              const SizedBox(height: 12),
              _MenuButton(
                label: '调试功能',
                icon: Icons.bug_report_outlined,
                onTap: () {
                  final builder = widget.debugBuilder;
                  if (builder != null) {
                    Navigator.push(context, MaterialPageRoute(builder: builder));
                  }
                },
              ),
              const Spacer(),
              if (_checked && !_backendOnline)
                Text(
                  '无法连接后端服务，请检查网络',
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
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
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
          child: Row(
            children: [
              Icon(icon, size: 26),
              const SizedBox(width: 14),
              Expanded(
                child: Text(
                  label,
                  style: Theme.of(context).textTheme.titleMedium,
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
    );
  }
}
