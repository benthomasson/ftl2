"""FTL2 production coverage collection — opt-in via FTL2_COVERAGE=1.

Collects coverage from the controller process and (when available) from
remote gate processes.  No hard dependency on coverage.py — everything
degrades to a silent no-op when the package is absent.

To enable:  FTL2_COVERAGE=1 ftl2 run ...
Custom dir: FTL2_COVERAGE_DIR=~/my-cov ftl2 run ...
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def is_coverage_enabled() -> bool:
    """Return True when coverage collection is opted-in."""
    return os.environ.get("FTL2_COVERAGE", "") == "1"


def coverage_dir() -> Path:
    """Return (and create) the directory for coverage data files."""
    d = Path(os.environ.get("FTL2_COVERAGE_DIR", "~/.ftl/coverage")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


class ControllerCoverage:
    """Context manager that wraps the controller process with coverage.py.

    Usage::

        with ControllerCoverage():
            cli()
    """

    def __init__(self) -> None:
        self._cov = None

    def __enter__(self) -> "ControllerCoverage":
        try:
            import coverage

            data_file = str(coverage_dir() / f".coverage.controller.{os.getpid()}")
            self._cov = coverage.Coverage(data_file=data_file)
            self._cov.start()
            logger.debug("Controller coverage started: %s", data_file)
        except ImportError:
            logger.debug("coverage package not installed — skipping controller coverage")
        except Exception:
            logger.debug("Failed to start controller coverage", exc_info=True)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._cov is not None:
            try:
                self._cov.stop()
                self._cov.save()
                logger.debug("Controller coverage saved")
            except Exception:
                logger.debug("Failed to save controller coverage", exc_info=True)


async def retrieve_gate_coverage(
    conn,
    remote_path: str,
    host_name: str,
) -> bool:
    """SFTP a gate coverage file back to the controller.

    Args:
        conn: Active asyncssh SSHClientConnection
        remote_path: Absolute path to .coverage file on the remote host
        host_name: Logical host name (used in the local filename)

    Returns:
        True if retrieval succeeded, False otherwise
    """
    try:
        local = coverage_dir() / f".coverage.gate.{host_name}.{os.getpid()}"
        async with conn.start_sftp_client() as sftp:
            await sftp.get(remote_path, str(local))
        logger.debug("Gate coverage retrieved: %s -> %s", remote_path, local)
        return True
    except Exception:
        logger.debug("Failed to retrieve gate coverage from %s", host_name, exc_info=True)
        return False
