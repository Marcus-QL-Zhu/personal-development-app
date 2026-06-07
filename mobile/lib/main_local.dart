import 'package:flutter/material.dart';

import 'app.dart';
import 'development/demo_development_repository.dart';

const _backendUrl = String.fromEnvironment(
  'GAMEVOICE_BACKEND_URL',
  defaultValue: 'http://localhost:8010',
);

const _apiToken = String.fromEnvironment('GAMEVOICE_API_TOKEN');

const _useDemoRepository = bool.fromEnvironment(
  'GAMEVOICE_USE_DEMO_REPOSITORY',
  defaultValue: true,
);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    GameVoiceApp(
      backendUrl: _backendUrl,
      apiToken: _apiToken,
      developmentRepository: _useDemoRepository
          ? DemoDevelopmentRepository()
          : null,
    ),
  );
}
