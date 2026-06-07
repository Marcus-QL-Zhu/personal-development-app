import 'package:flutter/material.dart';

import 'development_repository.dart';

class DevelopmentCoachDetailScreen extends StatelessWidget {
  const DevelopmentCoachDetailScreen({super.key, required this.session});

  final DevelopmentCoachingSession session;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('coach详情')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(session.topic, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                Chip(label: Text(session.coachDate)),
                Chip(label: Text(session.qualityStatus)),
                Chip(label: Text(session.syncStatus)),
              ],
            ),
            const SizedBox(height: 16),
            _DetailSection(title: '内容总结', content: session.contentSummary),
            _DetailSection(title: 'Action Plan', content: session.actionPlan),
            _DetailSection(title: 'Manager Notes', content: session.managerFeedback),
            const SizedBox(height: 8),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('查看完整转写'),
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text(session.transcriptText),
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
