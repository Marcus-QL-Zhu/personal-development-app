import 'package:flutter/material.dart';

import 'development_repository.dart';

class DevelopmentCoachDetailScreen extends StatefulWidget {
  const DevelopmentCoachDetailScreen({
    super.key,
    required this.session,
    required this.repository,
  });

  final DevelopmentCoachingSession session;
  final DevelopmentRepository repository;

  @override
  State<DevelopmentCoachDetailScreen> createState() =>
      _DevelopmentCoachDetailScreenState();
}

class _DevelopmentCoachDetailScreenState
    extends State<DevelopmentCoachDetailScreen> {
  late DevelopmentCoachingSession _session;
  bool _retryingSync = false;
  String _retryError = '';

  @override
  void initState() {
    super.initState();
    _session = widget.session;
  }

  Future<void> _retrySync() async {
    setState(() {
      _retryingSync = true;
      _retryError = '';
    });
    try {
      final updated = await widget.repository.retryCoachingSessionSync(
        employeeId: _session.employeeId,
        sessionId: _session.id,
      );
      if (!mounted) return;
      setState(() {
        _session = updated;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _retryError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _retryingSync = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final syncFailed = _session.syncStatus == 'failed';
    final syncError = _retryError.isNotEmpty ? _retryError : _session.syncError;
    return Scaffold(
      appBar: AppBar(title: const Text('coach详情')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(_session.topic, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                Chip(label: Text(_session.coachDate)),
                Chip(label: Text(_session.qualityStatus)),
                Chip(label: Text(_session.syncStatus)),
              ],
            ),
            if (syncFailed) ...[
              const SizedBox(height: 12),
              if (syncError.isNotEmpty)
                Text(
                  syncError,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: FilledButton.icon(
                  onPressed: _retryingSync ? null : _retrySync,
                  icon: Icon(
                    _retryingSync ? Icons.hourglass_empty : Icons.sync,
                  ),
                  label: Text(_retryingSync ? '同步中' : '重新同步飞书'),
                ),
              ),
            ],
            const SizedBox(height: 16),
            _DetailSection(title: '内容总结', content: _session.contentSummary),
            _DetailSection(title: 'Action Plan', content: _session.actionPlan),
            _DetailSection(
              title: 'Manager Notes',
              content: _session.managerFeedback,
            ),
            const SizedBox(height: 8),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('查看完整转写'),
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text(_session.transcriptText),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailSection extends StatelessWidget {
  const _DetailSection({required this.title, required this.content});

  final String title;
  final String content;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(content.isEmpty ? '暂无内容' : content),
        ],
      ),
    );
  }
}
