import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Never touch the real user cache during tests; run Qt without a display.
_tmp = tempfile.mkdtemp(prefix="vdbx_test_")
os.environ["LOCALAPPDATA"] = _tmp
os.environ["XDG_CACHE_HOME"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def vital_path(tmp_path_factory):
    from tests.synthetic import make_vital_file
    p = tmp_path_factory.mktemp("vital") / "case.vital"
    make_vital_file(p)
    return str(p)


@pytest.fixture()
def offline_case(vital_path, monkeypatch):
    """Route every case request to the local synthetic .vital file."""
    from vitaldb_explorer import data_access as da
    monkeypatch.setattr(da, "case_source", lambda case_id: vital_path)
    da.clear_cache()
    return vital_path


@pytest.fixture()
def store(offline_case):
    from vitaldb_explorer import data_access as da
    return da.CaseStore(da.fetch_case_header(1, use_cache=False))
