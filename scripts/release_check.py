"""Run local release checks without publishing or contacting providers."""

from pathlib import Path

from offline_agent.release import release_check

if __name__ == "__main__":
    try:
        print(release_check(Path(__file__).resolve().parents[1]))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
