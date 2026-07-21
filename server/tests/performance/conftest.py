import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def pytest_configure(config: object) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: requires a migrated PostgreSQL database configured for integration tests",
    )
