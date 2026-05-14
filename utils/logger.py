"""
utils/logger.py
===============
Structured logging for the pipeline.
"""

from __future__ import annotations
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def set_debug(debug: bool = True) -> None:
    logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)
