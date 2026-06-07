import 'package:flutter/material.dart';

import '../backend/gamevoice_repository.dart';

class ConversationListView extends StatefulWidget {
  const ConversationListView({
    super.key,
    required this.events,
    required this.assistantName,
  });

  final List<ContextEventRecord> events;
  final String assistantName;

  @override
  State<ConversationListView> createState() => _ConversationListViewState();
}

class _ConversationListViewState extends State<ConversationListView> {
  final ScrollController _scrollController = ScrollController();
  bool _stickToBottom = true;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_handleScroll);
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
  }

  @override
  void didUpdateWidget(covariant ConversationListView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.events.length != widget.events.length && _stickToBottom) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
    }
  }

  @override
  void dispose() {
    _scrollController.removeListener(_handleScroll);
    _scrollController.dispose();
    super.dispose();
  }

  void _handleScroll() {
    if (!_scrollController.hasClients) return;
    final distanceFromBottom =
        _scrollController.position.maxScrollExtent - _scrollController.offset;
    final shouldStick = distanceFromBottom < 80;
    if (shouldStick != _stickToBottom) {
      setState(() => _stickToBottom = shouldStick);
    }
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
  }

  @override
  Widget build(BuildContext context) {
    if (widget.events.isEmpty) {
      return const Center(child: Text('暂无对话'));
    }
    return Stack(
      children: [
        ListView.separated(
          key: const Key('tabletop-log-list'),
          controller: _scrollController,
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 16),
          itemCount: widget.events.length,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (context, index) {
            final event = widget.events[index];
            return _ContextEventTile(
              key: ValueKey(
                  'context-event-$index-${event.kind}-${event.content.hashCode}'),
              event: event,
              assistantName: widget.assistantName,
            );
          },
        ),
        if (!_stickToBottom)
          Positioned(
            right: 12,
            bottom: 12,
            child: FilledButton.icon(
              onPressed: _scrollToBottom,
              icon: const Icon(Icons.keyboard_arrow_down),
              label: const Text('回到底部'),
            ),
          ),
      ],
    );
  }
}

class _ContextEventTile extends StatelessWidget {
  const _ContextEventTile({
    super.key,
    required this.event,
    required this.assistantName,
  });

  final ContextEventRecord event;
  final String assistantName;

  bool get _isAssistant =>
      event.kind.startsWith('assistant') || event.source == 'companion';

  bool get _isUnspoken => event.kind == 'assistant_unspoken';

  bool get _isRuleReference => event.kind == 'rule_reference';

  String get _speakerName {
    if (_isRuleReference) return '\u67e5\u8be2\u7ed3\u679c';
    if (_isAssistant) return assistantName;
    if (event.source == 'live_asr' || event.kind == 'voice_transcript') {
      return '玩家';
    }
    return event.source.isEmpty ? '记录' : event.source;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colorScheme = theme.colorScheme;
    final accent = _isRuleReference
        ? colorScheme.secondary
        : _isAssistant
            ? colorScheme.primary
            : colorScheme.tertiary;
    final background = _isUnspoken || _isRuleReference
        ? colorScheme.surfaceContainerLowest
        : colorScheme.surfaceContainerLow;
    final muted = _isUnspoken || _isRuleReference;

    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(8),
        border: Border(
          left: BorderSide(
            color: muted ? colorScheme.outlineVariant : accent,
            width: 4,
          ),
        ),
      ),
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 9),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 10,
                backgroundColor: accent.withValues(alpha: muted ? 0.16 : 0.24),
                child: Icon(
                  _isRuleReference
                      ? Icons.manage_search
                      : _isAssistant
                          ? Icons.auto_awesome
                          : Icons.person,
                  size: 12,
                  color: accent,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _speakerName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: colorScheme.onSurfaceVariant,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              if (_isUnspoken)
                Text(
                  '未说完',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: colorScheme.outline,
                  ),
                ),
              if (_isRuleReference)
                Text(
                  '\u672a\u64ad\u62a5',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: colorScheme.outline,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            event.content,
            style: theme.textTheme.bodyMedium?.copyWith(
              color:
                  muted ? colorScheme.onSurfaceVariant : colorScheme.onSurface,
              height: 1.35,
              fontStyle: muted ? FontStyle.italic : FontStyle.normal,
            ),
          ),
        ],
      ),
    );
  }
}
