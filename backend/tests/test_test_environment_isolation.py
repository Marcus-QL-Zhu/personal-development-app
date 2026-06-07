import gamevoice_server.main as main_module
from gamevoice_server.table_store import InMemoryTableStore


def test_backend_tests_do_not_use_real_runtime_database():
    assert isinstance(main_module._get_table_store(), InMemoryTableStore)
