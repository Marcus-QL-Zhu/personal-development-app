import 'package:flutter/material.dart';

import '../backend/gamevoice_repository.dart';

class FileLibrary extends StatelessWidget {
  const FileLibrary({
    super.key,
    required this.documents,
    required this.selectedFilename,
    required this.onSelect,
    required this.onReadSummary,
  });

  final List<DocumentRecord> documents;
  final String? selectedFilename;
  final ValueChanged<String> onSelect;
  final VoidCallback onReadSummary;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Document library', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            if (documents.isEmpty)
              const Text('No files uploaded yet.')
            else
              ...documents.map(
                (document) => RadioListTile<String>(
                  value: document.filename,
                  groupValue: selectedFilename,
                  title: Text(document.filename),
                  subtitle: Text(document.status),
                  onChanged: (value) {
                    if (value != null) {
                      onSelect(value);
                    }
                  },
                ),
              ),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerLeft,
              child: FilledButton(
                onPressed: selectedFilename == null ? null : onReadSummary,
                child: const Text('Read summary'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
