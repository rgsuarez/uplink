"""Check GBNF syntax with an existing llama.cpp library, without a vocabulary or model."""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Inspect pinned source/docs without loading any native library or model",
    )
    parser.add_argument("--source-dir", type=Path, default=Path("artifacts/f3-native-source"))
    parser.add_argument(
        "--fetch-source",
        action="store_true",
        help="Fetch only missing hash-pinned source/docs, never weights",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.source_only:
        if args.library is not None:
            parser.error("--source-only cannot load --library")
        from offline_agent.native_audit import write_audit

        directory = args.source_dir if args.source_dir.is_absolute() else root / args.source_dir
        try:
            print(write_audit(root, directory, fetch=args.fetch_source))
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Refused: {exc}\n")
        return
    if args.library is None or args.fetch_source:
        parser.error("Choose --source-only or the existing --library syntax check")
    library_path: Path = args.library.resolve(strict=True)
    native = ctypes.CDLL(str(library_path))
    sampler = native.llama_sampler_init_grammar
    sampler.restype = ctypes.c_void_p
    sampler.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)
    native.llama_sampler_free.argtypes = (ctypes.c_void_p,)
    native.llama_sampler_free.restype = None
    grammar = (root / "schemas/tools.gbnf").read_bytes()
    verdicts: list[bool] = []
    for candidate in (grammar, b"root ::= ???"):
        # This API only parses character grammar here; a null vocabulary cannot load weights.
        pointer: int | None = sampler(None, candidate, b"root")
        verdicts.append(pointer is not None)
        if pointer is not None:
            native.llama_sampler_free(pointer)
    report = {
        "model_weights_loaded": False,
        "runtime_library_sha256": hashlib.sha256(library_path.read_bytes()).hexdigest(),
        "grammar_sha256": hashlib.sha256(grammar).hexdigest(),
        "valid_grammar_accepted": verdicts[0],
        "invalid_control_rejected": not verdicts[1],
    }
    target = root / "artifacts/native-grammar-check.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not verdicts[0] or verdicts[1]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
