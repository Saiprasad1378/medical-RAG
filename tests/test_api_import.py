import sys
from pathlib import Path
# Ensure project root on sys.path for bare `pytest C:\path\to\tests\...` invocations
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_api_imports():
    import api
    assert hasattr(api, "app")
