from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]


def test_production_workers_share_export_volume_but_split_long_export_queue() -> None:
    compose = (REPO_ROOT / "compose.production.yaml").read_text()
    general_worker = compose.split("  worker:\n", 1)[1].split(
        "  export-worker:\n",
        1,
    )[0]
    export_worker = compose.split("  export-worker:\n", 1)[1].split(
        "  scheduler:\n",
        1,
    )[0]

    assert "--queues=default,analysis,notifications" in general_worker
    assert "--queues=exports" not in general_worker
    assert "export-files:/var/lib/farescope/exports" in general_worker
    assert "--queues=exports" in export_worker
    assert "export-files:/var/lib/farescope/exports" in export_worker
