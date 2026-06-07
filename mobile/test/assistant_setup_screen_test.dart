import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:personal_development_app/backend/demo_gamevoice_repository.dart';
import 'package:personal_development_app/screens/assistant_setup_screen.dart';

void main() {
  setUpAll(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('renders initially visible setup fields', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AssistantSetupScreen(repository: DemoGameVoiceRepository()),
      ),
    );
    await tester.pumpAndSettle();

    // Verify the screen renders and key sections are visible
    expect(find.text('助手名称'), findsOneWidget);
    expect(find.text('人格模板'), findsOneWidget);
    expect(find.text('温柔体贴型'), findsOneWidget);
    expect(find.text('幽默风趣型'), findsOneWidget);

    // Verify at least one TextField is present (description is below viewport)
    expect(find.byType(TextField), findsOneWidget);

  });
}
