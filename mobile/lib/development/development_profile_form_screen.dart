import 'package:flutter/material.dart';

import 'development_repository.dart';

class DevelopmentProfileFormScreen extends StatefulWidget {
  const DevelopmentProfileFormScreen({
    super.key,
    required this.repository,
    this.employee,
  });

  final DevelopmentRepository repository;
  final DevelopmentEmployee? employee;

  @override
  State<DevelopmentProfileFormScreen> createState() =>
      _DevelopmentProfileFormScreenState();
}

class _DevelopmentProfileFormScreenState
    extends State<DevelopmentProfileFormScreen> {
  final _nameController = TextEditingController();
  final _profileController = TextEditingController();
  final _gallupController = TextEditingController();
  bool _saving = false;
  String? _error;

  bool get _isEdit => widget.employee != null;

  @override
  void initState() {
    super.initState();
    final employee = widget.employee;
    if (employee != null) {
      _nameController.text = employee.name;
      _profileController.text = employee.profileNote;
      _gallupController.text = employee.gallupRaw;
    }
  }

  Future<void> _save() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) {
      setState(() => _error = '请输入顾问名称');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final employee = widget.employee;
      if (employee == null) {
        await widget.repository.createEmployee(
          name: name,
          gallupRaw: _gallupController.text,
          profileNote: _profileController.text,
        );
      } else {
        await widget.repository.updateEmployee(
          employeeId: employee.id,
          name: name,
          gallupRaw: _gallupController.text,
          profileNote: _profileController.text,
        );
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已保存')),
      );
      Navigator.pop(context, true);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    _profileController.dispose();
    _gallupController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_isEdit ? '编辑履历' : '新增顾问')),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
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
                  TextField(
                    controller: _nameController,
                    maxLength: 80,
                    decoration: const InputDecoration(
                      labelText: '名称',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _ScrollableTextField(
                    controller: _profileController,
                    label: '介绍',
                    maxLength: 1000,
                    height: 150,
                  ),
                  const SizedBox(height: 12),
                  _ScrollableTextField(
                    controller: _gallupController,
                    label: 'Gallup 34 排序',
                    maxLength: 3000,
                    height: 190,
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save),
                  label: const Text('确认保存'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ScrollableTextField extends StatefulWidget {
  const _ScrollableTextField({
    required this.controller,
    required this.label,
    required this.maxLength,
    required this.height,
  });

  final TextEditingController controller;
  final String label;
  final int maxLength;
  final double height;

  @override
  State<_ScrollableTextField> createState() => _ScrollableTextFieldState();
}

class _ScrollableTextFieldState extends State<_ScrollableTextField> {
  final _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: widget.height,
      child: Scrollbar(
        controller: _scrollController,
        thumbVisibility: true,
        child: TextField(
          controller: widget.controller,
          scrollController: _scrollController,
          maxLength: widget.maxLength,
          maxLines: null,
          expands: true,
          textAlignVertical: TextAlignVertical.top,
          decoration: InputDecoration(
            labelText: widget.label,
            alignLabelWithHint: true,
            border: const OutlineInputBorder(),
          ),
        ),
      ),
    );
  }
}
