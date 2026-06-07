from gamevoice_server.live_diagnostics_store import LiveDiagnosticsStore


def test_audio_receive_diagnostics_track_inter_arrival_and_bursts():
    store = LiveDiagnosticsStore()

    store.mark_audio_chunk_received("table-1", 3200, monotonic_ms=1000.0)
    store.mark_audio_chunk_received("table-1", 3200, monotonic_ms=1020.0)
    store.mark_audio_chunk_received("table-1", 3200, monotonic_ms=1040.0)

    snapshot = store.snapshot("table-1")

    assert snapshot["audio_chunks_received"] == 3
    assert snapshot["audio_bytes_received"] == 9600
    assert snapshot["audio_receive_monotonic_ms"] == 1040.0
    assert snapshot["audio_inter_arrival_ms"] == 20.0
    assert snapshot["receive_burst_count"] == 2
    assert snapshot["max_receive_burst_chunks_per_second"] == 3
    assert "last_audio_chunk_bytes" not in snapshot
    assert "recent_audio_chunk_bytes" not in snapshot


def test_audio_send_diagnostics_track_queue_lag_and_send_cost():
    store = LiveDiagnosticsStore()

    store.mark_audio_enqueue("table-1", queue_depth=7)
    store.mark_audio_dequeue("table-1", queue_depth=6, send_worker_lag_ms=180.5)
    store.mark_audio_send_complete(
        "table-1",
        send_audio_elapsed_ms=205.25,
        tencent_payload_send_elapsed_ms=3.5,
        send_audio_pacing_requested_ms=200.0,
        send_audio_pacing_actual_ms=4672.0,
    )
    store.mark_event_loop_lag("table-1", lag_ms=512.5)

    snapshot = store.snapshot("table-1")

    assert snapshot["audio_queue_depth_on_enqueue"] == 7
    assert snapshot["audio_queue_depth_on_dequeue"] == 6
    assert snapshot["send_worker_lag_ms"] == 180.5
    assert snapshot["max_send_worker_lag_ms"] == 180.5
    assert snapshot["send_audio_elapsed_ms"] == 205.25
    assert snapshot["max_send_audio_elapsed_ms"] == 205.25
    assert snapshot["tencent_payload_send_elapsed_ms"] == 3.5
    assert snapshot["max_tencent_payload_send_elapsed_ms"] == 3.5
    assert snapshot["send_audio_pacing_requested_ms"] == 200.0
    assert snapshot["send_audio_pacing_actual_ms"] == 4672.0
    assert snapshot["max_send_audio_pacing_actual_ms"] == 4672.0
    assert snapshot["event_loop_lag_ms"] == 512.5
    assert snapshot["max_event_loop_lag_ms"] == 512.5
