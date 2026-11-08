"""The real localhost smoke server must never inherit a PostgreSQL target."""
import os
from pathlib import Path
import subprocess
import sys


def test_http_smoke_ignores_external_database_url(tmp_path):
    root = Path(__file__).resolve().parent.parent
    environment = os.environ.copy()
    # An intentionally unusable local test URL, never real account details.
    environment["COMMERCE_DATABASE_URL"] = "postgresql://unused:unused@127.0.0.1:1/not_a_database"
    inherited_path = tmp_path / "must_not_be_used.db"
    environment["COMMERCE_DB"] = str(inherited_path)
    result = subprocess.run([sys.executable, "-m", "scripts.smoke_http"], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP smoke passed:" in result.stdout
    assert "integer bounds" in result.stdout
    assert not inherited_path.exists()
