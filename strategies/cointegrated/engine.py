"""Cointegrated strategy runtime stub for freeze-scope phase."""

from __future__ import annotations

from utils.logger import get_logger


log = get_logger("cointegrated_engine")


class CointegratedEngine:
    def start(self, pair_check_only: bool = False) -> None:
        if pair_check_only:
            log.info("Pair-check mode enabled. Full pair finder implementation is pending phase 2.")
            return
        log.info("Pure cointegrated branch active.")
        log.info("Legacy grid engine removed in freeze-scope.")
        log.info("Execution engine implementation starts in next phase.")
