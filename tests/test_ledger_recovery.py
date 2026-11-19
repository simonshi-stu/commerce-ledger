"""SQLite-only verification and backup/restore rejection of bad ledger semantics."""
from pathlib import Path
import pytest
from app import recovery
from test_backend import api
from test_ledger_reconciliation import CASES, inject_balanced_history


@pytest.mark.parametrize("case,expected", CASES)
@pytest.mark.parametrize("operation", ["verify", "backup", "restore"])
def test_balanced_bad_history_cannot_be_verified_or_published(api, tmp_path, case, expected, operation):
    client, db = api
    inject_balanced_history(client, db, case)
    target = tmp_path / "rejected.db"
    with pytest.raises(recovery.VerificationError, match="business reconciliation"):
        if operation == "verify":
            recovery.verify_database(db.path)
        else:
            recovery.snapshot_database(db.path, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".commerce-snapshot-*"))
    assert Path(db.path).exists()


def test_recovery_cli_fails_closed_without_echoing_history(api, tmp_path, capsys):
    client, db = api
    inject_balanced_history(client, db, "orphan_sale")
    for operation in ["backup", "restore"]:
        target = tmp_path / (operation + ".db")
        assert recovery.main([operation, "--source", db.path, "--output", str(target)]) == 1
        output = capsys.readouterr().out
        assert '"error": "VerificationError"' in output
        assert "synthetic" not in output and str(tmp_path) not in output
        assert not target.exists()
