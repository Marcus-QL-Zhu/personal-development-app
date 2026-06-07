import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../backend/gamevoice_repository.dart';

typedef PickFilesCallback = Future<List<UploadFilePayload>> Function();

class FilePickerButton extends StatelessWidget {
  const FilePickerButton({
    super.key,
    required this.onFilesSelected,
    this.pickFiles = _pickFilesFromDevice,
  });

  final ValueChanged<List<UploadFilePayload>> onFilesSelected;
  final PickFilesCallback pickFiles;

  static Future<List<UploadFilePayload>> _pickFilesFromDevice() async {
    final result = await FilePicker.platform.pickFiles(
      allowMultiple: true,
      withData: true,
    );
    if (result == null) {
      return const [];
    }

    return result.files
        .where((file) => file.bytes != null)
        .map(
          (file) => UploadFilePayload(
            filename: file.name,
            bytes: file.bytes!,
          ),
        )
        .toList();
  }

  Future<void> _handlePick() async {
    final files = await pickFiles();
    if (files.isNotEmpty) {
      onFilesSelected(files);
    }
  }

  @override
  Widget build(BuildContext context) {
    return OutlinedButton(
      onPressed: _handlePick,
      child: const Text('Upload files'),
    );
  }
}
