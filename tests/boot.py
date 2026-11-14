import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from boot import BootError, BootManager, HEADER


class PrivateBoot(BootManager):
    def filesystem(self):
        return "TEST-BOOT-UUID", Path("/")


class BootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manager = PrivateBoot(self.root / "boot")
        self.manager.grub.parent.mkdir(parents=True)
        self.original = b"set timeout=5\nmenuentry 'Legacy Vix' { true; }\n"
        self.manager.grub.write_bytes(self.original)

    def tearDown(self):
        self.temporary.cleanup()

    def system(self, name, version):
        system = self.root / name
        asset = self.root / f"asset-{name}"
        runtime = asset / "runtime"
        (runtime / "modules" / version).mkdir(parents=True)
        (asset / "kernel").write_bytes(f"kernel-{version}".encode())
        (asset / "initrd").write_bytes(f"initrd-{version}".encode())
        (asset / "boot.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "kernel_version": version,
                    "runtime": str(runtime),
                    "kernel": str(asset / "kernel"),
                    "initrd": str(asset / "initrd"),
                    "root_device": "UUID=ROOT-UUID",
                    "kernel_params": [
                        "quiet",
                        "console=ttyS0,115200n8",
                        "label=with space",
                    ],
                }
            )
        )
        system.mkdir()
        (system / "boot").symlink_to(asset)
        return system

    def test_adopt_update_and_keep_legacy_fallback(self):
        first = self.system("first", "6.12.1")
        second = self.system("second", "6.12.2")
        snapshot = self.manager.snapshot(None, first)
        self.manager.activate(first, 1, {1: first})
        self.assertEqual(self.manager.original.read_bytes(), self.original)
        self.assertTrue(self.manager.grub.read_text().startswith(HEADER))
        self.manager.activate(second, 2, {1: first, 2: second})
        text = self.manager.grub.read_text()
        self.assertLess(text.index("generation 2"), text.index("generation 1"))
        self.assertIn("vix.generation=2", text)
        self.assertIn("root=UUID=ROOT-UUID", text)
        self.assertIn("grub.cfg.vix-original", text)
        subprocess.run(["grub-script-check", str(self.manager.grub)], check=True)
        self.manager.restore(snapshot)
        self.assertEqual(self.manager.grub.read_bytes(), self.original)

    def test_rollback_selects_the_retained_pair(self):
        first = self.system("first", "6.12.1")
        second = self.system("second", "6.12.2")
        self.manager.activate(first, 1, {1: first})
        self.manager.activate(second, 2, {1: first, 2: second})
        self.manager.activate(first, 1, {1: first, 2: second})
        text = self.manager.grub.read_text()
        self.assertLess(text.index("generation 1"), text.index("generation 2"))
        self.assertEqual(self.manager.original.read_bytes(), self.original)
        subprocess.run(["grub-script-check", str(self.manager.grub)], check=True)

    def test_kernel_management_can_return_to_the_original_loader(self):
        first = self.system("first", "6.12.1")
        disabled = self.root / "disabled"
        disabled.mkdir()
        self.manager.activate(first, 1, {1: first})
        self.manager.activate(disabled, 2, {1: first, 2: disabled})
        self.assertEqual(self.manager.grub.read_bytes(), self.original)

    def test_partial_or_modified_assets_cannot_be_selected(self):
        first = self.system("first", "6.12.1")
        self.manager.activate(first, 1, {1: first})
        before = self.manager.grub.read_bytes()
        installed = self.manager.stage(first)
        (installed / "kernel").write_bytes(b"damaged")
        with self.assertRaisesRegex(BootError, "modified"):
            self.manager.activate(first, 1, {1: first})
        self.assertEqual(self.manager.grub.read_bytes(), before)

    def test_boot_without_separate_partition_and_with_separate_partition(self):
        first = self.system("first", "6.12.1")
        self.manager.activate(first, 1, {1: first})
        self.assertIn(
            str(self.manager.directory / "vix/kernels"), self.manager.grub.read_text()
        )
        self.manager.filesystem = lambda: ("SEPARATE-BOOT", self.manager.directory)
        self.manager.activate(first, 1, {1: first})
        text = self.manager.grub.read_text()
        self.assertIn("($vix_boot)/vix/kernels/", text)
        self.assertNotIn(str(self.manager.directory / "vix/kernels"), text)
        subprocess.run(["grub-script-check", str(self.manager.grub)], check=True)

    def test_service_only_generations_do_not_require_grub(self):
        system = self.root / "services-only"
        system.mkdir()
        no_boot = BootManager(self.root / "not-a-boot-filesystem")
        self.assertIsNone(no_boot.snapshot(None, system))
        self.assertFalse(no_boot.directory.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
