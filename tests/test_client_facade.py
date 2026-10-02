"""D1-01 回归：三端经 `DataSentry` 门面取数，不直取 `client._store`。

- 门面可用性：`pii_vault/list_pii_mappings/count_pii_mappings/delete_pii_mapping`；
- 棘轮：`src/datasentry/` 内 `client._store` / `_client._store` 残留仅限
  `RuleProposalService` 两处（后续迁 `client.rules_service()` 后归零）。
"""

from __future__ import annotations

from pathlib import Path

from datasentry import DataSentry
from datasentry.pii_vault import PIIVault

_APP = Path(__file__).resolve().parents[1] / "src" / "datasentry"
_REMAINING_ALLOWLIST = 2  # cli.py RuleProposalService ×2


def _facade_violations() -> list[str]:
    hits: list[str] = []
    for path in sorted(_APP.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "client._store" in line or "_client._store" in line:
                hits.append(f"{path.name}:{lineno}:{line.strip()}")
    return hits


class TestClientFacade:
    def test_pii_facades(self, tmp_path: Path) -> None:
        client = DataSentry(tmp_path / "ws")
        try:
            vault = client.pii_vault()
            assert isinstance(vault, PIIVault)
            assert client.list_pii_mappings() == []
            assert client.count_pii_mappings() == 0
            assert client.delete_pii_mapping("pii_nope") is False
        finally:
            client.close()

    def test_store_violations_ratchet(self) -> None:
        hits = _facade_violations()
        assert len(hits) <= _REMAINING_ALLOWLIST, hits
        assert all("RuleProposalService" in h for h in hits), hits
