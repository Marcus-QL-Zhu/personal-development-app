import 'dart:async';

import 'package:flutter/material.dart';

import '../audio/voice_recorder.dart';
import '../backend/gamevoice_repository.dart';
import 'development_coach_detail_screen.dart';
import 'development_repository.dart';

class DevelopmentCoachHistoryScreen extends StatefulWidget {
  const DevelopmentCoachHistoryScreen({
    super.key,
    required this.repository,
    required this.employee,
    this.voiceRecorderFactory,
  });

  final DevelopmentRepository repository;
  final DevelopmentEmployee employee;
  final VoiceRecorder Function()? voiceRecorderFactory;

  @override
  State<DevelopmentCoachHistoryScreen> createState() =>
      _DevelopmentCoachHistoryScreenState();
}

class _DevelopmentCoachHistoryScreenState
    extends State<DevelopmentCoachHistoryScreen> {
  late final VoiceRecorder _recorder;
  final Stopwatch _stopwatch = Stopwatch();
  Timer? _timer;
  List<DevelopmentCoachingSession> _sessions = [];
  bool _loading = true;
  bool _recording = false;
  bool _processing = false;
  String? _error;
  UploadFilePayload? _failedClip;

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
      final loaded = await widget.repository.listCoachingSessions(
        employeeId: widget.employee.id,
      );
      final sessions = [...loaded];
      sessions.sort((a, b) => _sortKey(b).compareTo(_sortKey(a)));
      if (mounted) setState(() => _sessions = sessions);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _sortKey(DevelopmentCoachingSession session) {
    return '${session.coachDate}-${session.id}';
  }

  Future<void> _startRecording() async {
    final granted = await _recorder.ensurePermission();
    if (!granted) {
      setState(() => _error = '没有麦克风权限');
      return;
    }
    await _recorder.start();
    _stopwatch
      ..reset()
      ..start();
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
    if (mounted) setState(() => _recording = true);
  }

  Future<void> _stopRecording() async {
    final clip = await _recorder.stop();
    _stopwatch.stop();
    _timer?.cancel();
    if (mounted) setState(() => _recording = false);
    if (clip == null || !mounted) return;
    final upload = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('上传这段录音?'),
        content: Text(clip.filename),
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
      await _upload(clip);
    }
  }

  Future<void> _upload(UploadFilePayload clip) async {
    setState(() {
      _processing = true;
      _error = null;
    });
    try {
      final session = await widget.repository.uploadCoachingSession(
        employeeId: widget.employee.id,
        clip: clip,
      );
      final next = [
        session,
        ..._sessions.where((item) => item.id != session.id)
      ];
      next.sort((a, b) => _sortKey(b).compareTo(_sortKey(a)));
      if (mounted) {
        setState(() {
          _sessions = next;
          _failedClip = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _failedClip = clip;
        });
      }
    } finally {
      if (mounted) setState(() => _processing = false);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final elapsed = _stopwatch.elapsed;
    final elapsedText =
        '${elapsed.inMinutes.toString().padLeft(2, '0')}:${(elapsed.inSeconds % 60).toString().padLeft(2, '0')}';
    return Scaffold(
      appBar: AppBar(title: Text('${widget.employee.name} 的 coach 历史')),
      body: SafeArea(
        child: Column(
          children: [
            if (_recording)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.all(16),
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  border: Border.all(color: Colors.red.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    Icon(Icons.fiber_manual_record, color: Colors.red.shade700),
                    const SizedBox(width: 8),
                    const Text('正在录音'),
                    const Spacer(),
                    Text(elapsedText),
                  ],
                ),
              ),
            if (_processing) const LinearProgressIndicator(),
            if (_processing)
              const Padding(
                padding: EdgeInsets.all(8),
                child: Text('处理中，请稍候'),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                    if (_failedClip != null) ...[
                      const SizedBox(height: 8),
                      OutlinedButton.icon(
                        onPressed:
                            _processing ? null : () => _upload(_failedClip!),
                        icon: const Icon(Icons.refresh),
                        label: const Text('重试上传'),
                      ),
                    ],
                  ],
                ),
              ),
            Expanded(
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : _sessions.isEmpty
                      ? const Center(child: Text('暂无 coach 记录'))
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 8, 16, 96),
                          itemCount: _sessions.length,
                          itemBuilder: (context, index) {
                            final session = _sessions[index];
                            return Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: ListTile(
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(8),
                                  side: BorderSide(
                                    color: Theme.of(context).dividerColor,
                                  ),
                                ),
                                title: Text(
                                    '${session.coachDate} · ${session.topic}'),
                                subtitle: Text(
                                  '${session.syncStatus} · ${session.qualityStatus}',
                                ),
                                trailing: const Icon(Icons.chevron_right),
                                onTap: () => Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                    builder: (_) =>
                                        DevelopmentCoachDetailScreen(
                                            session: session),
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _processing
                      ? null
                      : _recording
                          ? _stopRecording
                          : _startRecording,
                  icon: Icon(_recording ? Icons.stop : Icons.mic),
                  label: Text(_recording ? '结束录音' : '开始录音'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
