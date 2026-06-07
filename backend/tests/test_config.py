from gamevoice_server.config import Settings, _load_dotenv_file


def test_tencent_realtime_chunk_bytes_defaults_to_200ms_websocket_packet_size():
    settings = Settings()

    assert settings.tencent_realtime_chunk_bytes == 6400


def test_gamevoice_db_path_defaults_to_runtime_sqlite_file():
    settings = Settings()

    assert settings.gamevoice_db_path == ".runtime/gamevoice.db"


def test_backend_silence_gate_defaults_are_conservative():
    settings = Settings()

    assert settings.live_silence_gate_enabled is True
    assert settings.live_silence_gate_frame_ms == 20
    assert settings.live_silence_gate_vad_mode == 1
    assert settings.live_silence_gate_preroll_ms == 300
    assert settings.live_silence_gate_speech_start_window_ms == 200
    assert settings.live_silence_gate_speech_start_voiced_ms == 60
    assert settings.live_silence_gate_hangover_ms == 700


def test_live_heartbeat_defaults_to_three_to_five_minutes():
    settings = Settings()

    assert settings.live_heartbeat_enabled is True
    assert settings.live_heartbeat_min_seconds == 180
    assert settings.live_heartbeat_max_seconds == 300


def test_personal_development_provider_defaults_are_configurable(monkeypatch):
    monkeypatch.setenv("MINIMAX_REASONING_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MINIMAX_REASONING_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
    monkeypatch.setenv("MINIMAX_REASONING_THINKING_TYPE", "adaptive")
    monkeypatch.setenv("MINIMAX_REASONING_SPLIT", "true")
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "base-token")
    monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "table-id")
    monkeypatch.setenv("PERSONAL_DEVELOPMENT_AUDIO_RETENTION_DAYS", "90")

    settings = Settings()

    assert settings.minimax_reasoning_model == "MiniMax-M3"
    assert settings.minimax_reasoning_base_url == "https://api.minimaxi.com/v1/chat/completions"
    assert settings.minimax_reasoning_thinking_type == "adaptive"
    assert settings.minimax_reasoning_split is True
    assert settings.feishu_bitable_app_token == "base-token"
    assert settings.feishu_bitable_table_id == "table-id"
    assert settings.personal_development_audio_retention_days == 90


def test_dotenv_file_supplies_missing_settings_without_overriding_shell_env(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'MINIMAX_API_KEY="from-dotenv"',
                "SILICONFLOW_API_KEY=from-dotenv",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "from-shell")

    _load_dotenv_file(env_file)

    settings = Settings()
    assert settings.minimax_api_key == "from-dotenv"
    assert settings.siliconflow_api_key == "from-shell"
