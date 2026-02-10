"""FTL2 telemetry — tracks run count only.

Sends only the application name and git commit hash to Segment.
No user information. No system information.

If you want to disable telemetry, fork this repo and delete this file
and the call to phone_home() in cli.py.
"""

import atexit
import uuid

WRITE_KEY = "haXw8AZ0x06563tTahJi6kOJxPLqMC79"


def _get_git_hash() -> str:
    """Get the git commit hash of the installed version."""
    # Try importlib.metadata direct_url.json (git+https installs)
    try:
        import importlib.metadata

        dist = importlib.metadata.distribution("ftl2")
        for f in dist.files or []:
            if f.name == "direct_url.json":
                import json

                data = json.loads(f.read_text())
                commit = data.get("vcs_info", {}).get("commit_id")
                if commit:
                    return commit
    except Exception:
        pass

    # Try git rev-parse HEAD (dev installs from checkout)
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback to package version
    from ftl2 import __version__

    return __version__


def phone_home() -> None:
    """Send a single telemetry event to Segment. Fire and forget."""
    try:
        import segment.analytics as analytics

        analytics.write_key = WRITE_KEY
        atexit.register(analytics.shutdown)
        analytics.track(
            anonymous_id=str(uuid.uuid4()),
            event="ftl2_run",
            properties={
                "name": "ftl2",
                "version": _get_git_hash(),
            },
        )
    except Exception:
        pass  # Never crash the tool for telemetry
