import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../diagnostics/mobile_diagnostics_logger.dart';

class LiveTranscriptEvent {
  const LiveTranscriptEvent({
    required this.event,
    this.sliceType,
    this.index,
    this.text = '',
    this.message,
    this.mode,
    this.source,
    this.content,
    this.lead,
    this.tail,
    this.turnId,
    this.replyId,
    this.speechJobId,
    this.ttsStreamId,
    this.speakerIdentities = const [],
    this.speakerIdentityReviewCandidates = const [],
    this.speakerIdentityBatch,
    this.liveSessionState,
  });

  final String event;
  final int? sliceType;
  final int? index;
  final String text;
  final String? message;
  final String? mode;
  final String? source;
  final String? content;
  final String? lead;
  final String? tail;
  final String? turnId;
  final String? replyId;
  final String? speechJobId;
  final String? ttsStreamId;
  final List<Map<String, dynamic>> speakerIdentities;
  final List<Map<String, dynamic>> speakerIdentityReviewCandidates;
  final Map<String, dynamic>? speakerIdentityBatch;
  final Map<String, dynamic>? liveSessionState;

  static List<Map<String, dynamic>> _mapList(Object? value) {
    final raw = value;
    if (raw is! List) {
      return const [];
    }
    return raw
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item.cast<String, dynamic>()))
        .toList(growable: false);
  }

  static Map<String, dynamic>? _mapValue(Object? value) {
    if (value is Map) {
      return Map<String, dynamic>.from(value.cast<String, dynamic>());
    }
    return null;
  }

  factory LiveTranscriptEvent.fromJson(Map<String, dynamic> json) {
    final identityBatch = _mapValue(json['speaker_identity_batch']);
    return LiveTranscriptEvent(
      event: json['event'] as String,
      sliceType: json['slice_type'] as int?,
      index: json['index'] as int?,
      text: json['text'] as String? ?? '',
      message: json['message'] as String?,
      mode: json['mode'] as String?,
      source: json['source'] as String?,
      content: json['content'] as String?,
      lead: json['lead'] as String?,
      tail: json['tail'] as String?,
      turnId: json['turn_id'] as String?,
      replyId: json['reply_id'] as String?,
      speechJobId: (json['speech_job'] as Map<String, dynamic>?)?['job_id'] as String?,
      ttsStreamId: (json['tts_stream'] as Map<String, dynamic>?)?['stream_id'] as String?,
      speakerIdentities: _mapList(json['speaker_identities'] ?? identityBatch?['speaker_identities']),
      speakerIdentityReviewCandidates: _mapList(
        json['speaker_identity_review_candidates'] ?? identityBatch?['speaker_identity_review_candidates'],
      ),
      speakerIdentityBatch: identityBatch,
      liveSessionState: _mapValue(json['live_session_state']),
    );
  }
}

typedef LiveTranscriptCallback = void Function(LiveTranscriptEvent value);

abstract class LiveTranscriptionClient {
  Future<void> connect({
    required String tableId,
    required LiveTranscriptCallback onEvent,
  });

  Future<void> sendAudio(List<int> chunk);

  Future<void> end();

  Future<void> close();
}

class WsLiveTranscriptionClient implements LiveTranscriptionClient {
  WsLiveTranscriptionClient({
    required this.backendLabel,
    String? apiToken,
  }) : _apiToken = apiToken?.trim() ?? '';

  final String backendLabel;
  final String _apiToken;
  WebSocket? _socket;
  StreamSubscription? _subscription;
  Completer<void>? _finalEventCompleter;
  int _sentAudioChunks = 0;

  @override
  Future<void> connect({
    required String tableId,
    required LiveTranscriptCallback onEvent,
  }) async {
    final uri = Uri.parse(backendLabel);
    final wsScheme = uri.scheme == 'https' ? 'wss' : 'ws';
    final wsUri = uri.replace(
      scheme: wsScheme,
      path: '/ws/tables/$tableId/listen',
      queryParameters: _apiToken.isEmpty
          ? null
          : {'access_token': _apiToken},
    );
    _socket = await WebSocket.connect(wsUri.toString());
    debugPrint('[LIVE][ws] connected to $wsUri');
    MobileDiagnostics.record(
      component: 'ws',
      event: 'connected',
      details: {'uri': wsUri.toString()},
    );
    _finalEventCompleter = Completer<void>();
    _subscription = _socket!.listen(
      (dynamic data) {
        if (data is! String) {
          return;
        }
        try {
          final json = jsonDecode(data) as Map<String, dynamic>;
          final event = LiveTranscriptEvent.fromJson(json);
          MobileDiagnostics.record(
            component: 'ws',
            event: 'event_received',
            details: {
              'event': event.event,
              if (event.sliceType != null) 'slice_type': event.sliceType,
              if (event.text.isNotEmpty) 'text_length': event.text.length,
            },
          );
          onEvent(event);
          if ((event.event == 'final' || event.event == 'error') &&
              !(_finalEventCompleter?.isCompleted ?? true)) {
            _finalEventCompleter?.complete();
          }
        } catch (_) {
          onEvent(
            const LiveTranscriptEvent(
              event: 'error',
              message: 'Invalid realtime payload from server',
            ),
          );
          if (!(_finalEventCompleter?.isCompleted ?? true)) {
            _finalEventCompleter?.complete();
          }
        }
      },
      onError: (_) {
        debugPrint('[LIVE][ws] socket error');
        MobileDiagnostics.record(component: 'ws', event: 'socket_error');
        onEvent(
          const LiveTranscriptEvent(
            event: 'error',
            message: 'Live socket disconnected',
          ),
        );
        if (!(_finalEventCompleter?.isCompleted ?? true)) {
          _finalEventCompleter?.complete();
        }
      },
      onDone: () {
        debugPrint('[LIVE][ws] socket closed');
        MobileDiagnostics.record(component: 'ws', event: 'socket_closed');
        onEvent(
          const LiveTranscriptEvent(
            event: 'error',
            message: 'Live socket closed',
          ),
        );
        if (!(_finalEventCompleter?.isCompleted ?? true)) {
          _finalEventCompleter?.complete();
        }
      },
      cancelOnError: true,
    );
  }

  @override
  Future<void> sendAudio(List<int> chunk) async {
    _sentAudioChunks += 1;
    if (_sentAudioChunks <= 3 || _sentAudioChunks % 20 == 0) {
      debugPrint('[LIVE][ws] send audio chunk #$_sentAudioChunks bytes=${chunk.length}');
      MobileDiagnostics.record(
        component: 'ws',
        event: 'audio_chunk_sent',
        details: {'chunk': _sentAudioChunks, 'bytes': chunk.length},
      );
    }
    _socket?.add(chunk);
  }

  @override
  Future<void> end() async {
    debugPrint('[LIVE][ws] end requested');
    MobileDiagnostics.record(component: 'ws', event: 'end_requested');
    _socket?.add(jsonEncode({'type': 'end'}));
    if (_finalEventCompleter != null && !(_finalEventCompleter?.isCompleted ?? true)) {
      await _finalEventCompleter!.future.timeout(const Duration(seconds: 5), onTimeout: () {});
    }
  }

  @override
  Future<void> close() async {
    debugPrint('[LIVE][ws] close requested');
    MobileDiagnostics.record(component: 'ws', event: 'close_requested');
    await _subscription?.cancel();
    await _socket?.close();
    _subscription = null;
    _socket = null;
    _finalEventCompleter = null;
    _sentAudioChunks = 0;
  }
}
