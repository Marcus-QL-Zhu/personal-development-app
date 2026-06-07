import 'dart:convert';
import 'dart:io';

import '../backend/gamevoice_repository.dart';
import 'development_repository.dart';

class HttpDevelopmentRepository implements DevelopmentRepository {
  HttpDevelopmentRepository({
    required this.baseUri,
    String? apiToken,
    HttpClient? httpClient,
  })  : _apiToken = apiToken?.trim() ?? '',
        _httpClient = httpClient ?? HttpClient();

  final Uri baseUri;
  final String _apiToken;
  final HttpClient _httpClient;

  @override
  Future<bool> healthCheck() async {
    final request = await _openRequest('GET', '/health');
    final response = await _readJson(request);
    return response['status'] == 'ok';
  }

  @override
  Future<List<DevelopmentEmployee>> listEmployees() async {
    final request = await _openRequest('GET', '/development/employees');
    final response = await _readJson(request);
    final employees = response['employees'] as List<dynamic>? ?? const [];
    return employees
        .whereType<Map<String, dynamic>>()
        .map(DevelopmentEmployee.fromJson)
        .toList();
  }

  @override
  Future<DevelopmentEmployee> createEmployee({
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final response = await _sendJson(
      method: 'POST',
      path: '/development/employees',
      payload: {
        'name': name,
        'gallup_raw': gallupRaw,
        'profile_note': profileNote,
      },
    );
    return DevelopmentEmployee.fromJson(response);
  }

  @override
  Future<DevelopmentEmployee> updateEmployee({
    required String employeeId,
    required String name,
    required String gallupRaw,
    required String profileNote,
  }) async {
    final response = await _sendJson(
      method: 'PUT',
      path: '/development/employees/$employeeId',
      payload: {
        'name': name,
        'gallup_raw': gallupRaw,
        'profile_note': profileNote,
      },
    );
    return DevelopmentEmployee.fromJson(response);
  }

  @override
  Future<List<DevelopmentCoachingSession>> listCoachingSessions({
    required String employeeId,
  }) async {
    final request = await _openRequest(
      'GET',
      '/development/employees/$employeeId/coaching-sessions',
    );
    final response = await _readJson(request);
    final sessions = response['sessions'] as List<dynamic>? ?? const [];
    return sessions
        .whereType<Map<String, dynamic>>()
        .map(DevelopmentCoachingSession.fromJson)
        .toList();
  }

  @override
  Future<DevelopmentCoachingSession> uploadCoachingSession({
    required String employeeId,
    required UploadFilePayload clip,
  }) async {
    final boundary = 'coach-audio-${DateTime.now().microsecondsSinceEpoch}';
    final request = await _openRequest(
      'POST',
      '/development/employees/$employeeId/coaching-sessions',
    );
    request.headers.set(
      HttpHeaders.contentTypeHeader,
      'multipart/form-data; boundary=$boundary',
    );

    request.write('--$boundary\r\n');
    request.write(_multipartContentDisposition(
      fieldName: 'clip',
      filename: clip.filename,
    ));
    request.write('Content-Type: application/octet-stream\r\n\r\n');
    request.add(clip.bytes);
    request.write('\r\n');
    request.write('--$boundary--\r\n');

    final response = await _readJson(request);
    return DevelopmentCoachingSession.fromJson(response);
  }

  Future<Map<String, dynamic>> _sendJson({
    required String method,
    required String path,
    required Map<String, dynamic> payload,
  }) async {
    final request = await _openRequest(method, path);
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode(payload));
    return _readJson(request);
  }

  Future<HttpClientRequest> _openRequest(String method, String path) {
    final uri = baseUri.replace(path: path);
    return _httpClient.openUrl(method, uri).then((request) {
      if (_apiToken.isNotEmpty) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_apiToken');
      }
      return request;
    });
  }

  Future<Map<String, dynamic>> _readJson(HttpClientRequest request) async {
    final response = await request.close();
    final body = await utf8.decodeStream(response);
    if (response.statusCode >= 400) {
      throw HttpException('Request failed: ${response.statusCode} $body');
    }
    return jsonDecode(body) as Map<String, dynamic>;
  }
}

String _multipartContentDisposition({
  required String fieldName,
  required String filename,
}) {
  final encoded = Uri.encodeComponent(filename);
  return 'Content-Disposition: form-data; name="$fieldName"; filename="upload.bin"; filename*=UTF-8\'\'$encoded\r\n';
}
