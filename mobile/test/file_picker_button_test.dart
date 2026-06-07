import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';
import 'package:personal_development_app/widgets/file_picker_button.dart';

void main() {
  testWidgets('file picker button forwards selected files', (tester) async {
    final forwarded = <UploadFilePayload>[];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: FilePickerButton(
            pickFiles: () async {
              return const [
                UploadFilePayload(
                  filename: 'scenario-a.txt',
                  bytes: [1, 2, 3],
                ),
              ];
            },
            onFilesSelected: forwarded.addAll,
          ),
        ),
      ),
    );

    await tester.tap(find.text('Upload files'));
    await tester.pumpAndSettle();

    expect(forwarded, hasLength(1));
    expect(forwarded.single.filename, 'scenario-a.txt');
  });
}
