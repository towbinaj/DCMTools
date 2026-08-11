"""Central logging configuration.

Logs go to a rotating file in the data dir and, optionally, to any GUI handler
that registers itself (see ``add_gui_handler``). A live-activity callback lets
tool panels stream progress into their own text areas.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import paths

_configured = False


def setup(level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger("dcmtoolkit")
    if _configured:
        return root

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        paths.log_dir() / "dcmtoolkit.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"dcmtoolkit.{name}")
