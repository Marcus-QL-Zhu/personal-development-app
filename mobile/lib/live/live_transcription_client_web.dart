import 'dart:async';

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

  factory LiveTranscriptEvent.fromJson(Map<String, dynamic> json) {
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
      speechJobId: (json['speech_job'] as Map<String, dynamic>?)?['job_id']
          as String?,
      ttsStreamId: (json['tts_stream'] as Map<String, dynamic>?)?['stream_id']
          as String?,
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
  }) {
    apiToken;
  }

  final String backendLabel;
  LiveTranscriptCallback? _onEvent;
  Timer? _timer;

  @override
  Future<void> connect({
    required String tableId,
    required LiveTranscriptCallback onEvent,
  }) async {
    _onEvent = onEvent;
    _timer = Timer(const Duration(milliseconds: 250), () {
      _onEvent?.call(
        const LiveTranscriptEvent(
          event: 'transcript',
          sliceType: 1,
          text: 'Browser demo live transcript',
        ),
      );
    });
  }

  @override
  Future<void> sendAudio(List<int> chunk) async {}

  @override
  Future<void> end() async {
    _onEvent?.call(
      const LiveTranscriptEvent(
        event: 'final',
        text: 'Browser demo final transcript',
      ),
    );
  }

  @override
  Future<void> close() async {
    _timer?.cancel();
    _timer = null;
    _onEvent = null;
  }
}
