import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../backend/gamevoice_repository.dart';

class VoiceClipButton extends StatelessWidget {
  const VoiceClipButton({
    super.key,
    required this.onClipSelected,
    this.pickVoiceClip = pickVoiceClipFromDevice,
  });

  final ValueChanged<UploadFilePayload> onClipSelected;
  final PickVoiceClipCallback pickVoiceClip;

  static Future<UploadFilePayload?> pickVoiceClipFromDevice() async {
    final result = await FilePicker.platform.pickFiles(
      allowMultiple: false,
      type: FileType.custom,
      allowedExtensions: const ['wav', 'mp3', 'm4a', 'aac', 'ogg'],
      withData: true,
    );
    final files = result?.files;
    if (files == null || files.isEmpty) {
      return null;
    }
    final file = files.first;
    if (file.bytes == null) {
      return null;
    }

    return UploadFilePayload(
      filename: file.name,
      bytes: file.bytes!,
    );
  }

  Future<void> _handlePick() async {
    final clip = await pickVoiceClip();
    if (clip != null) {
      onClipSelected(clip);
    }
  }

  @override
  Widget build(BuildContext context) {
    return OutlinedButton(
      onPressed: _handlePick,
      child: const Text('Upload voice clip'),
    );
  }
}
