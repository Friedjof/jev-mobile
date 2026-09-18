from __future__ import annotations

import grp
import os
from pathlib import Path
import stat
import subprocess


HOOK = Path(__file__).parents[1] / "deploy" / "synology" / "jev-mobile-usb-hook"


def _fixture(tmp_path: Path, vendor: str = "22b8", product: str = "2e82") -> tuple[dict[str, str], Path, Path]:
    sysfs = tmp_path / "sys" / "1-2"
    sysfs.mkdir(parents=True)
    (sysfs / "idVendor").write_text(vendor)
    (sysfs / "idProduct").write_text(product)
    (sysfs / "busnum").write_text("1")
    (sysfs / "devnum").write_text("7")
    node = tmp_path / "dev" / "001" / "007"
    node.parent.mkdir(parents=True)
    node.touch(mode=0o600)

    group = grp.getgrgid(os.getgid()).gr_name
    config = tmp_path / "usb-hook.env"
    config.write_text(
        "USB_VENDOR_ID='22b8'\n"
        "USB_PRODUCT_ID='2e82'\n"
        "ANDROID_SERIAL='PHONE123'\n"
        f"USB_GROUP='{group}'\n"
        "WORKER_CONTAINER='jev-mobile-worker'\n",
    )
    calls = tmp_path / "docker.calls"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        "if [ \"$1\" = exec ]; then echo device; fi\n",
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "JEV_MOBILE_USB_CONFIG": str(config),
        "JEV_USB_SYSFS_ROOT": str(tmp_path / "sys"),
        "JEV_USB_DEV_ROOT": str(tmp_path / "dev"),
        "JEV_DOCKER_BIN": str(docker),
        "ADB_VERIFY_ATTEMPTS": "1",
        "ADB_VERIFY_DELAY_SECONDS": "0",
    }
    return environment, node, calls


def test_matching_root_owned_style_usb_node_is_repaired_and_verified(tmp_path) -> None:
    environment, node, calls = _fixture(tmp_path)

    result = subprocess.run(
        [str(HOOK), "--repair", "1-2"], env=environment, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(node.stat().st_mode) == 0o660
    assert calls.read_text().splitlines() == [
        "restart jev-mobile-worker",
        "exec jev-mobile-worker adb -s PHONE123 get-state",
    ]


def test_non_matching_usb_node_is_ignored(tmp_path) -> None:
    environment, node, calls = _fixture(tmp_path, vendor="1234")

    result = subprocess.run(
        [str(HOOK), "--repair", "1-2"], env=environment, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert "Ignoring non-matching" in result.stdout
    assert stat.S_IMODE(node.stat().st_mode) == 0o600
    assert not calls.exists()
