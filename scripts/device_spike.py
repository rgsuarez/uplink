"""Run only after explicit device-load approval. This script is not executed during F2."""

import argparse
from pathlib import Path

from offline_agent.device_metrics import SpikeRefused
from offline_agent.device_spike import DeviceSettings, run_device_spike
from offline_agent.runtime_config import GGUF_NAME


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-load", action="store_true")
    parser.add_argument("--gguf", type=Path, default=root / "models" / GGUF_NAME)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    settings = DeviceSettings(args.gguf, args.server, args.allow_model_load, repeats=args.repeats)
    try:
        output = run_device_spike(root, settings)
    except SpikeRefused as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"Measured spike evidence: {output}")


if __name__ == "__main__":
    main()
