import 'package:flutter/material.dart';
import 'app.dart';

const _backendUrl = String.fromEnvironment(
  'GAMEVOICE_BACKEND_URL',
  defaultValue: 'http://192.168.71.58:8010',
);

const _apiToken = String.fromEnvironment('GAMEVOICE_API_TOKEN');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const GameVoiceApp(
    backendUrl: _backendUrl,
    apiToken: _apiToken,
  ));
}
