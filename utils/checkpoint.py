"""
utils/checkpoint.py
===================
Save and load pipeline state between stages.
Every stage saves its output as JSON before the next stage starts.
On re-run, completed stages are skipped automatically.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

log = get_logger("checkpoint")


class CheckpointManager:
    """
    Manages per-stage checkpoint files under output_dir/checkpoints/.

    File naming:
        stage_{N}_{name}.json       — stage output data
        stage_{N}_{name}.done       — sentinel: stage completed successfully
    """

    def __init__(self, output_dir: Path) -> None:
        self.ckpt_dir = output_dir / "checkpoints"
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_done(self, stage: int, name: str) -> bool:
        return self._done_path(stage, name).exists()

    def save(self, stage: int, name: str, data: Any) -> Path:
        """Atomically save data and mark stage done."""
        data_path = self._data_path(stage, name)
        self._atomic_write(data_path, data)
        # Write done sentinel
        done_path = self._done_path(stage, name)
        done_path.write_text(json.dumps({
            "stage": stage,
            "name":  name,
            "ts":    time.time(),
        }))
        log.info("Stage %d (%s) checkpoint saved → %s", stage, name, data_path.name)
        return data_path

    def load(self, stage: int, name: str) -> Any:
        """Load saved data for a completed stage."""
        data_path = self._data_path(stage, name)
        if not data_path.exists():
            raise FileNotFoundError(f"No checkpoint for stage {stage} ({name})")
        with open(data_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        log.info("Stage %d (%s) loaded from checkpoint", stage, name)
        return data

    def invalidate_from(self, stage: int) -> None:
        """Remove all checkpoints at or after stage N (for --from-stage)."""
        for path in self.ckpt_dir.iterdir():
            if not path.name.startswith("stage_"):
                continue
            try:
                stage_num = int(path.name.split("_")[1])
                if stage_num >= stage:
                    path.unlink()
                    log.debug("Removed checkpoint: %s", path.name)
            except (IndexError, ValueError):
                continue
        log.info("Invalidated checkpoints from stage %d onwards", stage)

    def clear_all(self) -> None:
        """Remove all checkpoints (for --fresh)."""
        for path in self.ckpt_dir.iterdir():
            try:
                path.unlink()
            except OSError:
                pass
        log.info("All checkpoints cleared")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _data_path(self, stage: int, name: str) -> Path:
        return self.ckpt_dir / f"stage_{stage:02d}_{name}.json"

    def _done_path(self, stage: int, name: str) -> Path:
        return self.ckpt_dir / f"stage_{stage:02d}_{name}.done"

    def _atomic_write(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        # fsync before rename for durability
        with open(tmp, "rb") as fh:
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(path)
