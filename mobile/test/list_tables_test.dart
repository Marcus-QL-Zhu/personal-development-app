import 'package:flutter_test/flutter_test.dart';
import 'package:personal_development_app/backend/gamevoice_repository.dart';

void main() {
  test('TableListItem.fromJson parses correctly', () {
    final json = {
      'id': 'table-123',
      'name': 'Test Table',
      'assistant_name': '宝子',
      'status': 'active',
      'created_at': '2026-05-06T10:00:00',
      'last_active_at': '2026-05-06T11:00:00',
      'personality_preview': '温柔体贴型',
    };
    final item = TableListItem.fromJson(json);
    expect(item.id, 'table-123');
    expect(item.name, 'Test Table');
    expect(item.assistantName, '宝子');
    expect(item.status, 'active');
    expect(item.createdAt, '2026-05-06T10:00:00');
    expect(item.lastActiveAt, '2026-05-06T11:00:00');
    expect(item.personalityPreview, '温柔体贴型');
  });
}
