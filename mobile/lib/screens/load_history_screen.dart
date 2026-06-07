import 'package:flutter/material.dart';
import '../backend/gamevoice_repository.dart';
import 'table_shell_screen.dart';

class LoadHistoryScreen extends StatefulWidget {
  const LoadHistoryScreen({
    super.key,
    required this.repository,
  });

  final GameVoiceRepository repository;

  @override
  State<LoadHistoryScreen> createState() => _LoadHistoryScreenState();
}

class _LoadHistoryScreenState extends State<LoadHistoryScreen> {
  List<TableListItem> _tables = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadTables();
  }

  Future<void> _loadTables() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final tables = await widget.repository.listTables();
      if (mounted) setState(() => _tables = tables);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  String _formatTime(String iso) {
    if (iso.isEmpty) return '-';
    try {
      final dt = DateTime.parse(iso);
      return '${dt.month}/${dt.day} ${dt.hour}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return iso;
    }
  }

  String _formatBytes(int bytes) {
    if (bytes < 1024 * 1024) {
      final kb = bytes / 1024;
      final value = kb == kb.roundToDouble()
          ? kb.toStringAsFixed(0)
          : kb.toStringAsFixed(1);
      return '$value KB';
    }
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  void _loadTable(TableListItem table) {
    final tableRecord = TableRecord(
      id: table.id,
      name: table.name,
      status: table.status,
      assistantName: table.assistantName,
    );
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TableShellScreen(
          table: tableRecord,
          repository: widget.repository,
        ),
      ),
    );
  }

  void _showTableMenu(BuildContext context, TableListItem table) {
    showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(0, 0, 0, 0),
      items: [
        const PopupMenuItem(value: 'rename', child: Text('重命名')),
        const PopupMenuItem(value: 'delete', child: Text('删除')),
      ],
    ).then((value) {
      if (value == 'rename') {
        _showRenameDialog(table);
      } else if (value == 'delete') {
        _confirmDelete(table);
      }
    });
  }

  Future<void> _showRenameDialog(TableListItem table) async {
    final controller = TextEditingController(text: table.name);
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('重命名'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            hintText: '输入新名字',
            border: OutlineInputBorder(),
          ),
          autofocus: true,
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
      try {
        await widget.repository.renameTable(table.id, result);
        _loadTables();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('重命名失败: $e')),
          );
        }
      }
    }
  }

  Future<void> _confirmDelete(TableListItem table) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除'),
        content: Text('确定删除 "${table.name}"？此操作不可恢复。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirm == true) {
      try {
        await widget.repository.deleteTable(table.id);
        _loadTables();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('删除失败: $e')),
          );
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('加载历史')),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text('加载失败: $_error'))
              : _tables.isEmpty
                  ? const Center(child: Text('暂无历史记录'))
                  : ListView.builder(
                      padding: const EdgeInsets.all(16),
                      itemCount: _tables.length,
                      itemBuilder: (context, index) {
                        final table = _tables[index];
                        return Card(
                          margin: const EdgeInsets.only(bottom: 12),
                          child: InkWell(
                            onTap: () => _loadTable(table),
                            onLongPress: () => _showTableMenu(context, table),
                            child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(table.name,
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium),
                                  const SizedBox(height: 4),
                                  Text('助手: ${table.assistantName}'),
                                  Text('创建: ${_formatTime(table.createdAt)}'),
                                  Text(
                                      '活跃: ${_formatTime(table.lastActiveAt)}'),
                                  if (table.personalityPreview.isNotEmpty)
                                    Text('人设: ${table.personalityPreview}'),
                                  if (table.documentCount > 0)
                                    Text(
                                      '附件 ${table.documentCount} 个 · ${_formatBytes(table.documentTotalBytes)}',
                                    ),
                                ],
                              ),
                            ),
                          ),
                        );
                      },
                    ),
      floatingActionButton: FloatingActionButton(
        onPressed: _loadTables,
        child: const Icon(Icons.refresh),
      ),
    );
  }
}
