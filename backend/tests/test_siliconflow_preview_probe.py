import json

from gamevoice_server import siliconflow_preview_probe as probe_module
from gamevoice_server.config import Settings


def test_siliconflow_preview_probe_writes_plain_text_summary_without_api_key(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def generate_preview_text(self, *, mode: str, transcript: str, events: list[dict]):
            return "plain preview"

    monkeypatch.setattr(probe_module, "SiliconFlowPreviewClient", FakeClient)

    summary = probe_module.run_siliconflow_preview_probe(
        settings_obj=Settings(siliconflow_api_key="secret-key"),
        transcript="rules question",
        mode="serious",
        output_dir=tmp_path,
    )

    summary_path = summary["summary_path"]
    saved = json.loads(open(summary_path, encoding="utf-8").read())

    assert saved["preview_text"] == "plain preview"
    assert "preview" not in saved
    assert saved["request_parameters"]["enable_thinking"] is False
    assert saved["request_parameters"]["max_tokens"] == 50
    assert "secret-key" not in json.dumps(saved, ensure_ascii=False)
