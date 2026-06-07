import 'package:flutter/material.dart';
import 'audio/audio_session_policy.dart';
import 'backend/gamevoice_repository.dart';
import 'backend/http_gamevoice_repository.dart';
import 'development/development_main_menu_screen.dart';
import 'development/development_repository.dart';
import 'development/http_development_repository.dart';
import 'screens/debug_functions_screen.dart';

class GameVoiceApp extends StatefulWidget {
  const GameVoiceApp({
    super.key,
    String? backendUrl,
    String? apiToken,
    GameVoiceRepository? repository,
    DevelopmentRepository? developmentRepository,
    ValueChanged<String>? onBackendUrlChanged,
  }) : _backendUrl = backendUrl ?? 'http://localhost:8010',
       _apiToken = apiToken ?? '',
       _repository = repository,
       _developmentRepository = developmentRepository,
       _onBackendUrlChanged = onBackendUrlChanged;

  final String _backendUrl;
  final String _apiToken;
  final GameVoiceRepository? _repository;
  final DevelopmentRepository? _developmentRepository;
  final ValueChanged<String>? _onBackendUrlChanged;

  @override
  State<GameVoiceApp> createState() => _GameVoiceAppState();
}

class _GameVoiceAppState extends State<GameVoiceApp> {
  late GameVoiceRepository _repository;
  late DevelopmentRepository _developmentRepository;

  @override
  void initState() {
    super.initState();
    _repository = widget._repository ??
        HttpGameVoiceRepository(
          baseUri: Uri.parse(widget._backendUrl),
          apiToken: widget._apiToken,
        );
    _developmentRepository = widget._developmentRepository ??
        HttpDevelopmentRepository(
          baseUri: Uri.parse(widget._backendUrl),
          apiToken: widget._apiToken,
        );
  }

  void _updateBackendUrl(String url) {
    setState(() {
      _repository = HttpGameVoiceRepository(
        baseUri: Uri.parse(url),
        apiToken: widget._apiToken,
      );
      _developmentRepository = HttpDevelopmentRepository(
        baseUri: Uri.parse(url),
        apiToken: widget._apiToken,
      );
    });
    widget._onBackendUrlChanged?.call(url);
  }

  @override
  Widget build(BuildContext context) {
    return GameVoiceAudioSessionHost(
      policy: GameVoiceAudioSessionPolicy(),
      child: MaterialApp(
        title: 'Personal Development App',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF274C77)),
          useMaterial3: true,
        ),
        home: DevelopmentMainMenuScreen(
          repository: _developmentRepository,
          debugBuilder: (_) => DebugFunctionsScreen(
            repository: _repository,
            onBackendUrlChanged: _updateBackendUrl,
          ),
        ),
      ),
    );
  }
}
