"""Check both supervised processes and dependency-aware application readiness."""
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen


def main():
    try:
        state = json.loads(Path("/app/instance/runtime-state.json").read_text())
        if set(state.get("pids", {})) != {"web", "worker"}:
            return 1
        for pid in state["pids"].values():
            if not isinstance(pid, int) or pid <= 1:
                return 1
            os.kill(pid, 0)
        request = Request("http://127.0.0.1:5050/api/v1/health/ready", headers={"Host": "localhost"})
        with urlopen(request, timeout=7) as response:
            payload = json.load(response)
            return 0 if response.status == 200 and payload.get("status") == "ready" else 1
    except Exception:
        # Docker's stored healthcheck output must never contain credentials.
        return 1


if __name__ == "__main__":
    sys.exit(main())
