"""Archive and frozen dependency boundaries, using only private scratch copies."""

from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path

import pytest

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import bench_package as package
from offline_agent.uplink import top10_contract as c

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sealed(tmp_path, manifest):
    root = tmp_path / "source"
    for name in c.obj(manifest["files"]):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    c.save(root / b.MANIFEST_PATH, manifest, exclusive=True)
    return root, manifest


def test_exact_archive_roundtrip_and_no_reseal(tmp_path, sealed) -> None:
    root, manifest = sealed
    output = tmp_path / ("bench-reduced-" + manifest["run_id"] + ".tar")
    digest = package.package(root, output)
    assert digest == package.verify_archive(root, output)
    with pytest.raises(FileExistsError):
        package.package(root, output)
    with tarfile.open(output) as archive:
        assert not any("lab.local" in name or name.endswith(".gguf") for name in archive.getnames())


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "bytes", "traversal"])
def test_altered_archive_refuses(tmp_path, sealed, mutation) -> None:
    root, manifest = sealed
    good = tmp_path / ("bench-reduced-" + manifest["run_id"] + ".tar")
    package.package(root, good)
    bad = tmp_path / "bad.tar"
    with tarfile.open(good) as source, tarfile.open(bad, "w") as target:
        for index, item in enumerate(source):
            content = source.extractfile(item).read()
            if index == 0:
                if mutation == "missing":
                    continue
                if mutation == "symlink":
                    item.type, item.linkname, item.size = tarfile.SYMTYPE, "/etc/passwd", 0
                    content = b""
                if mutation == "bytes":
                    content = b"x" * len(content)
                if mutation == "traversal":
                    item.name = "../escape"
            target.addfile(item, io.BytesIO(content))
        if mutation == "extra":
            item = tarfile.TarInfo("extra.py")
            target.addfile(item, io.BytesIO())
    with pytest.raises(c.Refused):
        package.verify_archive(root, bad)


def test_changed_frozen_dependency_refuses(sealed) -> None:
    root, _ = sealed
    (root / "src/offline_agent/uplink/top10_engine.py").write_text("changed")
    with pytest.raises(c.Refused, match="package_file_changed"):
        b.verify_manifest(root)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_exact_new_runtime_inventory_required(sealed, mutation) -> None:
    root, manifest = sealed
    files = c.obj(manifest["files"])
    if mutation == "missing":
        del files["src/offline_agent/uplink/bench_bridge.py"]
    else:
        files["extra.py"] = "a" * 64
    c.save(root / b.MANIFEST_PATH, manifest)
    with pytest.raises(c.Refused, match="exact_inventory"):
        b.verify_manifest(root)
