import 'package:flutter/material.dart';

import '../audio/voice_recorder.dart';
import '../backend/gamevoice_repository.dart';
import 'development_repository.dart';

class DevelopmentHomeScreen extends StatefulWidget {
  const DevelopmentHomeScreen({
    super.key,
    required this.repository,
    this.voiceRecorderFactory,
  });

  final DevelopmentRepository repository;
  final VoiceRecorder Function()? voiceRecorderFactory;

  @override
  State<DevelopmentHomeScreen> createState() => _DevelopmentHomeScreenState();
}

class _DevelopmentHomeScreenState extends State<DevelopmentHomeScreen> {
  final _nameController = TextEditingController();
  final _gallupController = TextEditingController();
  final _profileController = TextEditingController();

  late final VoiceRecorder _recorder;
  List<DevelopmentEmployee> _employees = [];
  List<DevelopmentCoachingSession> _sessions = [];
  DevelopmentEmployee? _selected;
  bool _loading = true;
  bool _saving = false;
  bool _recording = false;
  bool _uploading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _recorder = widget.voiceRecorderFactory?.call() ?? RecordVoiceRecorder();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final employees = await widget.repository.listEmployees();
      if (!mounted) return;
      setState(() {
        _employees = employees;
        if (_selected == null && employees.isNotEmpty) {
          _selectEmployee(employees.first, loadSessions: false);
        }
      });
      if (_selected != null) {
        await _loadSessions(_selected!.id);
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _selectEmployee(DevelopmentEmployee employee, {bool loadSessions = true}) {
    _selected = employee;
    _nameController.text = employee.name;
    _gallupController.text = employee.gallupRaw;
    _profileController.text = employee.profileNote;
    if (loadSessions) {
      _loadSessions(employee.id);
    }
  }

  Future<void> _loadSessions(String employeeId) async {
    final sessions = await widget.repository.listCoachingSessions(employeeId: employeeId);
    if (mounted) setState(() => _sessions = sessions);
  }

  Future<void> _saveEmployee() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) {
      setState(() => _error = '请输入员工姓名');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final selected = _selected;
      final employee = selected == null
          ? await widget.repository.createEmployee(
              name: name,
              gallupRaw: _gallupController.text,
              profileNote: _profileController.text,
            )
          : await widget.repository.updateEmployee(
              employeeId: selected.id,
              name: name,
              gallupRaw: _gallupController.text,
              profileNote: _profileController.text,
            );
      if (!mounted) return;
      await _load();
      setState(() {
        _selectEmployee(employee);
      });
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _startRecording() async {
    final employee = _selected;
    if (employee == null) {
      setState(() => _error = '请先保存或选择员工');
      return;
    }
    final granted = await _recorder.ensurePermission();
    if (!granted) {
      setState(() => _error = '没有麦克风权限');
      return;
    }
    await _recorder.start();
    if (mounted) setState(() => _recording = true);
  }

  Future<void> _stopRecording() async {
    final clip = await _recorder.stop();
    if (mounted) setState(() => _recording = false);
    if (clip == null || !mounted) return;
    final upload = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('上传这段录音?'),
        content: Text('${clip.filename} (${clip.bytes.length} bytes)'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('上传'),
          ),
        ],
      ),
    );
    if (upload == true) {
      await _uploadClip(clip);
    }
  }

  Future<void> _uploadClip(UploadFilePayload clip) async {
    final employee = _selected;
    if (employee == null) return;
    setState(() {
      _uploading = true;
      _error = null;
    });
    try {
      final session = await widget.repository.uploadCoachingSession(
        employeeId: employee.id,
        clip: clip,
      );
      if (mounted) {
        setState(() => _sessions = [
              session,
              ..._sessions.where((item) => item.id != session.id),
            ]);
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    _gallupController.dispose();
    _profileController.dispose();
    _recorder.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Personal Development'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (_error != null) _ErrorBanner(message: _error!),
                  _EmployeePicker(
                    employees: _employees,
                    selected: _selected,
                    onSelected: _selectEmployee,
                    onNew: () {
                      setState(() {
                        _selected = null;
                        _sessions = [];
                        _nameController.clear();
                        _gallupController.clear();
                        _profileController.clear();
                      });
                    },
                  ),
                  const SizedBox(height: 16),
                  _ProfileForm(
                    nameController: _nameController,
                    gallupController: _gallupController,
                    profileController: _profileController,
                    saving: _saving,
                    onSave: _saveEmployee,
                  ),
                  const SizedBox(height: 16),
                  _RecorderPanel(
                    hasEmployee: _selected != null,
                    recording: _recording,
                    uploading: _uploading,
                    onStart: _startRecording,
                    onStop: _stopRecording,
                  ),
                  const SizedBox(height: 16),
                  _SessionList(sessions: _sessions),
                ],
              ),
            ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      color: Theme.of(context).colorScheme.errorContainer,
      child: Text(message),
    );
  }
}

class _EmployeePicker extends StatelessWidget {
  const _EmployeePicker({
    required this.employees,
    required this.selected,
    required this.onSelected,
    required this.onNew,
  });

  final List<DevelopmentEmployee> employees;
  final DevelopmentEmployee? selected;
  final ValueChanged<DevelopmentEmployee> onSelected;
  final VoidCallback onNew;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final employee in employees)
          ChoiceChip(
            label: Text(employee.name),
            selected: selected?.id == employee.id,
            onSelected: (_) => onSelected(employee),
          ),
        ActionChip(
          avatar: const Icon(Icons.add, size: 18),
          label: const Text('新增员工'),
          onPressed: onNew,
        ),
      ],
    );
  }
}

class _ProfileForm extends StatelessWidget {
  const _ProfileForm({
    required this.nameController,
    required this.gallupController,
    required this.profileController,
    required this.saving,
    required this.onSave,
  });

  final TextEditingController nameController;
  final TextEditingController gallupController;
  final TextEditingController profileController;
  final bool saving;
  final VoidCallback onSave;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: nameController,
          decoration: const InputDecoration(
            labelText: '姓名',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: gallupController,
          minLines: 5,
          maxLines: 10,
          decoration: const InputDecoration(
            labelText: 'Gallup 34 排序',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: profileController,
          minLines: 3,
          maxLines: 6,
          decoration: const InputDecoration(
            labelText: '自然语言介绍',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: saving ? null : onSave,
          icon: saving
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.save),
          label: const Text('保存员工档案'),
        ),
      ],
    );
  }
}

class _RecorderPanel extends StatelessWidget {
  const _RecorderPanel({
    required this.hasEmployee,
    required this.recording,
    required this.uploading,
    required this.onStart,
    required this.onStop,
  });

  final bool hasEmployee;
  final bool recording;
  final bool uploading;
  final VoidCallback onStart;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: FilledButton.icon(
            onPressed: !hasEmployee || recording || uploading ? null : onStart,
            icon: const Icon(Icons.mic),
            label: const Text('开始录音'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: FilledButton.tonalIcon(
            onPressed: recording ? onStop : null,
            icon: const Icon(Icons.stop),
            label: const Text('停止录音'),
          ),
        ),
      ],
    );
  }
}

class _SessionList extends StatelessWidget {
  const _SessionList({required this.sessions});

  final List<DevelopmentCoachingSession> sessions;

  @override
  Widget build(BuildContext context) {
    if (sessions.isEmpty) {
      return const Text('暂无 coach 记录');
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Coach History', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        for (final session in sessions)
          Card(
            margin: const EdgeInsets.only(bottom: 12),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${session.coachDate} · ${session.topic}',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 8),
                  Text(session.contentSummary),
                  const SizedBox(height: 8),
                  Text('Action Plan: ${session.actionPlan}'),
                  const SizedBox(height: 8),
                  Text('Manager Feedback: ${session.managerFeedback}'),
                  const SizedBox(height: 8),
                  Text('转写: ${session.transcriptText}'),
                  Text('同步: ${session.syncStatus} · 质量: ${session.qualityStatus}'),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
