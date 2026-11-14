import json
import os
from pathlib import Path
import subprocess
import tempfile

boot = Path(os.environ["VIX_BOOT"])
descriptor = json.loads((boot / "boot.json").read_text())
version = descriptor["kernel_version"]
runtime = Path(descriptor["runtime"])
assert version.startswith("6.12.")
assert (runtime / "firmware/regulatory.db").is_file()
assert (boot / "kernel").stat().st_size > 1024 * 1024
assert (boot / "initrd").stat().st_size < 64 * 1024 * 1024

for suffix in ("", ".xz", ".gz", ".zst"):
    module = runtime / "modules" / version / f"updates/v4l2loopback.ko{suffix}"
    if module.is_file():
        break
else:
    raise AssertionError("declared extra module is absent")
vermagic = subprocess.check_output(
    ["modinfo", "-F", "vermagic", str(module)], text=True
)
assert vermagic.startswith(version + " "), (version, vermagic)

with tempfile.TemporaryFile() as archive:
    subprocess.run(["gzip", "-dc", str(boot / "initrd")], stdout=archive, check=True)
    archive.seek(0)
    contents = subprocess.check_output(
        ["cpio", "-it"], stdin=archive, text=True
    ).splitlines()
    for filename in ("ext4.ko", "virtio_blk.ko", "v4l2loopback.ko"):
        assert any(path.endswith("/" + filename) for path in contents), filename
print(
    f"Linux {version}: initramfs storage drivers, matching extra module and firmware verified"
)
