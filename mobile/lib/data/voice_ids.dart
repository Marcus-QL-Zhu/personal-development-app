class VoiceIdOption {
  final String voiceId;
  final String label;
  final String filename;

  const VoiceIdOption({
    required this.voiceId,
    required this.label,
    required this.filename,
  });
}

const voiceIdOptions = [
  VoiceIdOption(
    voiceId: '',
    label: '服务器默认音色',
    filename: '',
  ),
];

const defaultVoiceId = VoiceIdOption(
  voiceId: '',
  label: '服务器默认音色',
  filename: '',
);
