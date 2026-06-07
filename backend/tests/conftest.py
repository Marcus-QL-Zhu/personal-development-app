from pathlib import Path
import os
import sys
import tempfile

from fastapi.testclient import TestClient

os.environ["GAMEVOICE_TESTING"] = "1"
os.environ.pop("GAMEVOICE_DB_PATH", None)
os.environ["TENCENT_APP_ID"] = ""
os.environ["TENCENT_SECRET_ID"] = ""
os.environ["TENCENT_SECRET_KEY"] = ""
os.environ["MINIMAX_API_KEY"] = ""
os.environ["SILICONFLOW_API_KEY"] = ""
os.environ["METASO_API_KEY"] = ""
os.environ["GAMEVOICE_UPLOAD_DIR"] = tempfile.mkdtemp(prefix="gamevoice-test-uploads-")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gamevoice_server.audio_gateway import AudioGateway
from gamevoice_server.document_progress import DocumentProgress
from gamevoice_server.document_reader_worker import DocumentReaderWorker
from gamevoice_server.document_reading_store import DocumentReadingStore
from gamevoice_server.document_store import DocumentStore
from gamevoice_server.document_summarizer import DocumentSummarizer
from gamevoice_server.file_ingest import FileIngestor
from gamevoice_server.main import app
from gamevoice_server.session_manager import SessionManager


def pytest_configure():
    pass


import pytest


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_manager():
    return SessionManager()


@pytest.fixture
def audio_gateway():
    return AudioGateway()


@pytest.fixture
def document_store(tmp_path):
    return DocumentStore(root_dir=tmp_path)


@pytest.fixture
def file_ingest(document_store):
    return FileIngestor(document_store)


@pytest.fixture
def reading_store():
    return DocumentReadingStore()


@pytest.fixture
def progress_helper():
    return DocumentProgress()


@pytest.fixture
def document_reader(reading_store):
    return DocumentReaderWorker(reading_store, DocumentSummarizer())
