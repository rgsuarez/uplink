"""Run outputs remain contained when the asset volume is mounted through a symlink."""

from pathlib import Path

import pytest

from offline_agent.live_settings import is_new_run_output


@pytest.mark.parametrize("symlinked_volume", [False, True])
@pytest.mark.parametrize("runs_exist", [False, True])
def test_fresh_output_accepts_direct_or_symlinked_volume(
    tmp_path: Path, symlinked_volume: bool, runs_exist: bool
) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    mount = tmp_path / "assets"
    if symlinked_volume:
        mount.symlink_to(volume, target_is_directory=True)
    else:
        mount = volume
    runs = mount / "runs"
    if runs_exist:
        runs.mkdir()
    output = runs / "new-run"
    assert is_new_run_output(output, runs)
    assert not output.exists()
    assert runs.exists() is runs_exist


@pytest.mark.parametrize("case", ["existing", "outside", "symlink_escape"])
def test_existing_or_escaped_output_is_refused(tmp_path: Path, case: str) -> None:
    runs = tmp_path / "volume" / "runs"
    runs.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if case == "existing":
        output = runs / "old-run"
        output.mkdir()
    elif case == "outside":
        output = outside / "new-run"
    else:
        link = runs / "escape"
        link.symlink_to(outside, target_is_directory=True)
        output = link / "new-run"
    assert not is_new_run_output(output, runs)
    assert not (outside / "new-run").exists()


def test_missing_asset_volume_is_refused_without_creating_it(tmp_path: Path) -> None:
    volume = tmp_path / "absent-volume"
    runs = volume / "runs"
    assert not is_new_run_output(runs / "new-run", runs)
    assert not volume.exists()
