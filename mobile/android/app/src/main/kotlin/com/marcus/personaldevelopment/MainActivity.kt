package com.marcus.personaldevelopment

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioManager
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private lateinit var duplexController: DuplexAudioSessionController
    private lateinit var nativeLiveCaptureController: NativeLiveCaptureController

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        duplexController = DuplexAudioSessionController(applicationContext)
        nativeLiveCaptureController =
            NativeLiveCaptureController(
                applicationContext,
                flutterEngine.dartExecutor.binaryMessenger,
            )
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "gamevoice/duplex_audio_session",
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "activate" -> {
                    try {
                        duplexController.activate()
                        result.success(null)
                    } catch (exc: Exception) {
                        result.error("activate_failed", exc.message, null)
                    }
                }

                "deactivate" -> {
                    try {
                        duplexController.deactivate()
                        result.success(null)
                    } catch (exc: Exception) {
                        result.error("deactivate_failed", exc.message, null)
                    }
                }

                else -> result.notImplemented()
            }
        }
    }

    override fun onDestroy() {
        if (::duplexController.isInitialized) {
            duplexController.deactivate()
        }
        if (::nativeLiveCaptureController.isInitialized) {
            nativeLiveCaptureController.stop()
        }
        super.onDestroy()
    }
}

private class DuplexAudioSessionController(context: Context) {
    private val audioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val focusChangeListener = AudioManager.OnAudioFocusChangeListener {}
    private var audioFocusRequest: AudioFocusRequest? = null
    private var activeRefCount = 0
    private var previousMode: Int = AudioManager.MODE_NORMAL
    private var previousSpeakerphoneOn: Boolean = false

    fun activate() {
        activeRefCount += 1
        if (activeRefCount > 1) {
            return
        }
        previousMode = audioManager.mode
        previousSpeakerphoneOn = audioManager.isSpeakerphoneOn
        requestAudioFocus()
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        audioManager.isMicrophoneMute = false
        audioManager.isSpeakerphoneOn = true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val speaker = audioManager.availableCommunicationDevices.firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            }
            if (speaker != null) {
                audioManager.setCommunicationDevice(speaker)
            }
        }
    }

    fun deactivate() {
        if (activeRefCount == 0) {
            return
        }
        activeRefCount -= 1
        if (activeRefCount > 0) {
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice()
        }
        audioManager.isSpeakerphoneOn = previousSpeakerphoneOn
        audioManager.mode = previousMode
        abandonAudioFocus()
    }

    private fun requestAudioFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setOnAudioFocusChangeListener(focusChangeListener)
                .build()
            audioFocusRequest = request
            audioManager.requestAudioFocus(request)
        } else {
            @Suppress("DEPRECATION")
            audioManager.requestAudioFocus(
                focusChangeListener,
                AudioManager.STREAM_VOICE_CALL,
                AudioManager.AUDIOFOCUS_GAIN,
            )
        }
    }

    private fun abandonAudioFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            audioFocusRequest?.let(audioManager::abandonAudioFocusRequest)
            audioFocusRequest = null
        } else {
            @Suppress("DEPRECATION")
            audioManager.abandonAudioFocus(focusChangeListener)
        }
    }
}

private class NativeLiveCaptureController(
    context: Context,
    messenger: io.flutter.plugin.common.BinaryMessenger,
) : EventChannel.StreamHandler {
    private val tag = "GameVoiceLiveCapture"
    private val mainHandler = Handler(Looper.getMainLooper())
    private var eventSink: EventChannel.EventSink? = null
    @Volatile
    private var captureRequested = false
    @Volatile
    private var activeSessionId: String? = null
    @Volatile
    private var captureGeneration = 0
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    private val methodChannel: MethodChannel
    private val sampleRateHz = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioEncoding = AudioFormat.ENCODING_PCM_16BIT

    init {
        EventChannel(
            messenger,
            "gamevoice/native_live_audio_capture/stream",
        ).setStreamHandler(this)
        methodChannel = MethodChannel(
            messenger,
            "gamevoice/native_live_audio_capture",
        )
        methodChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    try {
                        start(sessionIdFrom(call.arguments))
                        result.success(null)
                    } catch (exc: Exception) {
                        result.error("native_capture_start_failed", exc.message, null)
                    }
                }

                "stop" -> {
                    try {
                        stop()
                        result.success(null)
                    } catch (exc: Exception) {
                        result.error("native_capture_stop_failed", exc.message, null)
                    }
                }

                else -> result.notImplemented()
            }
        }
    }

    override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
        val sessionId = sessionIdFrom(arguments)
        eventSink = events
        Log.i(tag, "onListen sessionId=$sessionId captureRequested=$captureRequested audioRecord=${audioRecord != null}")
        emitDiagnostic(
            "on_listen",
            mapOf(
                "session_id" to sessionId,
                "active_session_id" to activeSessionId,
                "capture_requested" to captureRequested,
                "audio_record" to (audioRecord != null),
                "generation" to captureGeneration,
            ),
        )
        // Call at the end so that eventSink is definitely set before
        // ensureCaptureRunning() evaluates the guard. This matters when
        // starting fresh after a stop: captureRequested may still be false
        // when onListen fires, but start() will be called right after,
        // at which point captureRequested will be true and eventSink will
        // already be populated.
        ensureCaptureRunning()
    }

    override fun onCancel(arguments: Any?) {
        val sessionId = sessionIdFrom(arguments)
        Log.i(tag, "onCancel sessionId=$sessionId activeSessionId=$activeSessionId")
        emitDiagnostic(
            "on_cancel",
            mapOf(
                "session_id" to sessionId,
                "active_session_id" to activeSessionId,
                "generation" to captureGeneration,
            ),
        )
        if (sessionId != null && activeSessionId != null && sessionId != activeSessionId) {
            emitDiagnostic(
                "on_cancel_ignored_stale",
                mapOf(
                    "session_id" to sessionId,
                    "active_session_id" to activeSessionId,
                    "generation" to captureGeneration,
                ),
            )
            return
        }
        eventSink = null
        stop(sessionId)
    }

    fun start(sessionId: String?) {
        captureRequested = true
        activeSessionId = sessionId
        captureGeneration += 1
        Log.i(tag, "start requested sessionId=$sessionId generation=$captureGeneration eventSink=${eventSink != null} audioRecord=${audioRecord != null}")
        emitDiagnostic(
            "start_requested",
            mapOf(
                "session_id" to sessionId,
                "generation" to captureGeneration,
                "event_sink" to (eventSink != null),
                "audio_record" to (audioRecord != null),
            ),
        )
        ensureCaptureRunning()
    }

    fun stop(sessionId: String? = activeSessionId) {
        if (sessionId != null && activeSessionId != null && sessionId != activeSessionId) {
            emitDiagnostic(
                "stop_ignored_stale",
                mapOf(
                    "session_id" to sessionId,
                    "active_session_id" to activeSessionId,
                    "generation" to captureGeneration,
                ),
            )
            return
        }
        captureRequested = false
        val stoppingGeneration = captureGeneration
        Log.i(tag, "stop requested sessionId=$sessionId generation=$stoppingGeneration")
        emitDiagnostic(
            "stop_requested",
            mapOf("session_id" to sessionId, "generation" to stoppingGeneration),
        )
        stopCaptureInternal(sessionId, stoppingGeneration)
        if (sessionId == activeSessionId || sessionId == null) {
            activeSessionId = null
        }
    }

    private fun ensureCaptureRunning() {
        if (!captureRequested || eventSink == null || audioRecord != null) {
            return
        }
        val sessionId = activeSessionId
        val generation = captureGeneration
        Log.i(tag, "ensureCaptureRunning starting recorder sessionId=$sessionId generation=$generation")
        emitDiagnostic(
            "ensure_capture_running",
            mapOf("session_id" to sessionId, "generation" to generation),
        )
        val minBufferSize =
            AudioRecord.getMinBufferSize(sampleRateHz, channelConfig, audioEncoding)
        if (minBufferSize <= 0) {
            throw IllegalStateException("Invalid AudioRecord buffer size: $minBufferSize")
        }
        val bufferSize = maxOf(minBufferSize, sampleRateHz / 5)
        val recorder =
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sampleRateHz,
                channelConfig,
                audioEncoding,
                bufferSize,
            )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IllegalStateException("AudioRecord failed to initialize")
        }
        Log.i(tag, "AudioRecord initialized bufferSize=$bufferSize")
        emitDiagnostic("audio_record_initialized", mapOf("buffer_size" to bufferSize))
        recorder.startRecording()
        Log.i(tag, "AudioRecord startRecording recordingState=${recorder.recordingState}")
        emitDiagnostic("audio_record_started", mapOf("recording_state" to recorder.recordingState))
        audioRecord = recorder
        val readBuffer = ByteArray(bufferSize)
        captureThread =
            Thread {
                var chunkCount = 0
                try {
                    while (
                        captureRequested &&
                            activeSessionId == sessionId &&
                            captureGeneration == generation &&
                            !Thread.currentThread().isInterrupted
                    ) {
                        val bytesRead = recorder.read(readBuffer, 0, readBuffer.size)
                        if (bytesRead > 0) {
                            chunkCount += 1
                            if (chunkCount <= 3 || chunkCount % 20 == 0) {
                                Log.i(tag, "AudioRecord read chunk#$chunkCount bytes=$bytesRead")
                                emitDiagnostic(
                                    "audio_record_read_chunk",
                                    mapOf("chunk" to chunkCount, "bytes" to bytesRead),
                                )
                            }
                            val chunk = readBuffer.copyOf(bytesRead)
                            mainHandler.post {
                                if (
                                    captureRequested &&
                                        activeSessionId == sessionId &&
                                        captureGeneration == generation
                                ) {
                                    eventSink?.success(chunk)
                                }
                            }
                        } else if (
                            bytesRead == AudioRecord.ERROR_INVALID_OPERATION ||
                                bytesRead == AudioRecord.ERROR_BAD_VALUE ||
                                bytesRead == AudioRecord.ERROR_DEAD_OBJECT
                        ) {
                            mainHandler.post {
                                eventSink?.error(
                                    "native_capture_read_failed",
                                    "AudioRecord read failed: $bytesRead",
                                    null,
                                )
                            }
                            Log.e(tag, "AudioRecord read failed: $bytesRead")
                            emitDiagnostic("audio_record_read_failed", mapOf("code" to bytesRead))
                            break
                        }
                    }
                } finally {
                    Log.i(tag, "capture thread finishing")
                    emitDiagnostic(
                        "capture_thread_finishing",
                        mapOf("session_id" to sessionId, "generation" to generation),
                    )
                    stopCaptureInternal(sessionId, generation)
                }
            }.apply {
                name = "GameVoiceNativeLiveCapture"
                start()
            }
    }

    private fun stopCaptureInternal(sessionId: String? = activeSessionId, generation: Int = captureGeneration) {
        if (generation != captureGeneration || sessionId != activeSessionId) {
            emitDiagnostic(
                "stop_capture_internal_ignored_stale",
                mapOf(
                    "session_id" to sessionId,
                    "active_session_id" to activeSessionId,
                    "generation" to generation,
                    "active_generation" to captureGeneration,
                ),
            )
            return
        }
        val thread = captureThread
        captureThread = null
        Log.i(tag, "stopCaptureInternal threadPresent=${thread != null}")
        emitDiagnostic(
            "stop_capture_internal",
            mapOf(
                "session_id" to sessionId,
                "generation" to generation,
                "thread_present" to (thread != null),
            ),
        )
        thread?.interrupt()
        val recorder = audioRecord
        audioRecord = null
        if (recorder != null) {
            try {
                if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    recorder.stop()
                }
            } catch (_: IllegalStateException) {
                // Ignore shutdown races.
            } finally {
                recorder.release()
            }
        }
    }

    private fun emitDiagnostic(event: String, details: Map<String, Any?> = emptyMap()) {
        mainHandler.post {
            methodChannel.invokeMethod(
                "diagnostic",
                mapOf("event" to event, "details" to details),
            )
        }
    }

    private fun sessionIdFrom(arguments: Any?): String? {
        val args = arguments as? Map<*, *> ?: return null
        return args["session_id"] as? String
    }
}
