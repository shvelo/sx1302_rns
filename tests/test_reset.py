"""Reset helper tests: environment propagation, output and failures."""

from pathlib import Path

import pytest

from sx1302_reticulum.config import BoardProfile
from sx1302_reticulum.reset import ResetError, run_reset


def _profile_with_script(write_profile, tmp_path: Path, script: Path, gpio: int):
    body = """
[board]
reset_enabled = yes
reset_script = {script}
reset_gpio = {gpio}

[rf]
frequency = 868100000
tx_power = 14

[channel]
bandwidth = 125000
spreadingfactor = 9

[txgain.12]
rf_power = 12
pa_gain = 1
pwr_idx = 4
""".format(script=script, gpio=gpio)
    return BoardProfile.from_ini(write_profile(body))


def test_reset_passes_gpio_and_arguments(write_profile, tmp_path, monkeypatch):
    output = tmp_path / "reset-env.txt"
    args = tmp_path / "reset-args.txt"
    script = tmp_path / "reset.sh"
    script.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "${SX1302_RESET_GPIO:-unset}" > "$OUT"\n'
        'printf "%s\\n" "$@" > "$ARGS"\n'
        'printf "reset output\\n"\n'
        'printf "reset warning\\n" >&2\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("OUT", str(output))
    monkeypatch.setenv("ARGS", str(args))
    profile = _profile_with_script(write_profile, tmp_path, script, 25)

    lines: list[str] = []
    run_reset(profile, "start", log=lines.append)

    assert output.read_text().strip() == "25"
    assert args.read_text().splitlines() == ["start"]
    assert any("reset output" in line for line in lines)
    assert any("reset warning" in line for line in lines)


def test_exported_gpio_overrides_profile(write_profile, tmp_path, monkeypatch):
    output = tmp_path / "reset-env.txt"
    script = tmp_path / "reset.sh"
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$SX1302_RESET_GPIO" > "$OUT"\n')
    script.chmod(0o755)
    monkeypatch.setenv("OUT", str(output))
    monkeypatch.setenv("SX1302_RESET_GPIO", "17")
    profile = _profile_with_script(write_profile, tmp_path, script, 25)

    run_reset(profile, "start")

    assert output.read_text().strip() == "17"


def test_failed_reset_raises(write_profile, tmp_path):
    script = tmp_path / "reset.sh"
    script.write_text("#!/bin/sh\nexit 7\n")
    script.chmod(0o755)
    profile = _profile_with_script(write_profile, tmp_path, script, 25)

    with pytest.raises(ResetError, match="exit code 7"):
        run_reset(profile, "start")


def test_hung_reset_times_out(write_profile, tmp_path):
    script = tmp_path / "reset.sh"
    script.write_text("#!/bin/sh\nsleep 2\n")
    script.chmod(0o755)
    profile = _profile_with_script(write_profile, tmp_path, script, 25)

    with pytest.raises(ResetError, match="timed out"):
        run_reset(profile, "start", timeout=0.05)


def test_disabled_reset_does_nothing(write_profile):
    profile = BoardProfile.from_ini(write_profile())

    lines: list[str] = []
    run_reset(profile, "start", log=lines.append)

    assert lines == ["Reset start: skipped (disabled in board profile)"]
