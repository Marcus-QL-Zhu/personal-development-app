import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/demo_gamevoice_repository.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';

void main() {
  test('demo repository supports browser UI smoke flows', () async {
    final repo = DemoGameVoiceRepository();

    expect(await repo.healthCheck(), isTrue);

    final table = await repo.createTable(
      'Local Browser Table',
      assistantName: 'Baozi',
    );

    final tables = await repo.listTables();
    final createdTable = tables.singleWhere((item) => item.id == table.id);
    expect(createdTable.name, 'Local Browser Table');

    final context = await repo.listContext(tableId: table.id);
    expect(context.map((event) => event.kind), contains('assistant_spoken'));

    final runtime = await repo.fetchRuntimeState(tableId: table.id);
    expect(runtime.state, 'idle');

    final stream = await repo.startTtsStream(
      tableId: table.id,
      jobId: 'demo-job-1',
    );
    final chunk = await repo.fetchNextTtsStreamChunk(
      tableId: table.id,
      streamId: stream.streamId,
    );

    expect(chunk, isNotNull);
    expect(chunk!.audioBytes, isNotEmpty);
    expect(chunk.isFinal, isTrue);
  });

  test('demo repository keeps uploaded files in table document list', () async {
    final repo = DemoGameVoiceRepository();
    final table = await repo.createTable('Demo Upload Table');

    await repo.uploadFiles(
      tableId: table.id,
      files: const [
        UploadFilePayload(filename: '桌游热词.txt', bytes: [1, 2, 3]),
      ],
    );

    final documents = await repo.listDocuments(table.id);
    expect(
      documents.map((document) => document.filename),
      contains('桌游热词.txt'),
    );
    expect(
      documents
          .singleWhere(
            (document) => document.filename == '桌游热词.txt',
          )
          .sizeBytes,
      3,
    );

    final tableItem =
        (await repo.listTables()).singleWhere((item) => item.id == table.id);
    expect(tableItem.documentCount, 3);
    expect(tableItem.documentTotalBytes, 3075);
  });

  test('demo repository removes deleted files from list and table stats',
      () async {
    final repo = DemoGameVoiceRepository();
    final table = await repo.createTable('Demo Delete Table');

    await repo.uploadFiles(
      tableId: table.id,
      files: const [
        UploadFilePayload(filename: '桌游热词.txt', bytes: [1, 2, 3]),
      ],
    );
    await repo.deleteDocument(tableId: table.id, filename: '桌游热词.txt');

    final documents = await repo.listDocuments(table.id);
    expect(
      documents.map((document) => document.filename),
      isNot(contains('桌游热词.txt')),
    );

    final tableItem =
        (await repo.listTables()).singleWhere((item) => item.id == table.id);
    expect(tableItem.documentCount, 2);
    expect(tableItem.documentTotalBytes, 3072);
  });
}
