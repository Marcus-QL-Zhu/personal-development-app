import 'package:flutter/material.dart';

import '../audio/voice_recorder.dart';
import 'development_coach_history_screen.dart';
import 'development_profile_form_screen.dart';
import 'development_repository.dart';

enum DevelopmentConsultantPickerMode { edit, history }

class DevelopmentConsultantPickerScreen extends StatefulWidget {
  const DevelopmentConsultantPickerScreen({
    super.key,
    required this.repository,
    required this.mode,
    this.voiceRecorderFactory,
  });

  final DevelopmentRepository repository;
  final DevelopmentConsultantPickerMode mode;
  final VoiceRecorder Function()? voiceRecorderFactory;

  @override
  State<DevelopmentConsultantPickerScreen> createState() =>
      _DevelopmentConsultantPickerScreenState();
}

class _DevelopmentConsultantPickerScreenState
    extends State<DevelopmentConsultantPickerScreen> {
  List<DevelopmentEmployee> _employees = [];
  bool _loading = true;
  String? _error;

  String get _title =>
      widget.mode == DevelopmentConsultantPickerMode.edit ? '编辑履历' : 'coach历史';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final employees = await widget.repository.listEmployees();
      if (mounted) setState(() => _employees = employees);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _open(DevelopmentEmployee employee) async {
    if (widget.mode == DevelopmentConsultantPickerMode.edit) {
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => DevelopmentProfileFormScreen(
            repository: widget.repository,
            employee: employee,
          ),
        ),
      );
      await _load();
      return;
    }
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => DevelopmentCoachHistoryScreen(
          repository: widget.repository,
          employee: employee,
          voiceRecorderFactory: widget.voiceRecorderFactory,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (_error != null)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: Text(
                          _error!,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ),
                    if (_employees.isEmpty)
                      const Padding(
                        padding: EdgeInsets.only(top: 120),
                        child: Center(child: Text('暂无顾问，请先新增顾问')),
                      ),
                    for (final employee in _employees)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: ListTile(
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(8),
                            side: BorderSide(color: Theme.of(context).dividerColor),
                          ),
                          title: Text(employee.name),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => _open(employee),
                        ),
                      ),
                  ],
                ),
              ),
      ),
    );
  }
}
