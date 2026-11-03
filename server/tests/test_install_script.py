"""Offline installer contract checks; never use the real home directory."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("bash") and shutil.which("curl"), "bash and curl required")
class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        source = self.base / "source.tar.gz"
        with tarfile.open(source, "w:gz") as archive:
            archive.add(ROOT / "server/pyproject.toml", arcname="source/server/pyproject.toml")
            archive.add(ROOT / "setup.sh", arcname="source/setup.sh")
        self.home = self.base / "home"
        self.env = dict(os.environ, HOME=str(self.home), OIU_INSTALL_ROOT=str(self.home / "runtime"),
                        OIU_BIN_DIR=str(self.home / "bin"), OIU_UNAME="Darwin", OIU_ARCHIVE_URL=source.as_uri())

    def run_install(self, *args):
        return subprocess.run(["bash", str(ROOT / "install.sh"), *args], env=self.env,
                              text=True, capture_output=True)

    def test_dry_run_has_plan_and_no_writes(self):
        result = self.run_install("--version", "vTEST", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("vTEST", self.env["OIU_ARCHIVE_URL"], str(self.home / "runtime"), str(self.home / "bin")):
            self.assertIn(expected, result.stdout)
        self.assertNotIn("helper", result.stdout.lower())
        self.assertFalse(self.home.exists())

    def test_offline_uninstall_delegates_to_managed_setup(self):
        runtime = self.home / "runtime"
        runtime.mkdir(parents=True)
        (runtime / ".open-iphone-use-managed").touch()
        (runtime / "setup.sh").write_text('#!/bin/sh\nprintf "delegated %s\\n" "$1"\n')
        result = self.run_install("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delegated --uninstall", result.stdout)

    def test_only_macos_is_supported(self):
        for os_name in ("Linux", "MINGW64_NT"):
            with self.subTest(os=os_name):
                self.env["OIU_UNAME"] = os_name
                result = self.run_install("--version", "vTEST", "--dry-run")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runs on macOS", result.stderr)
