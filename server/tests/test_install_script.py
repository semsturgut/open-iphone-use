"""Offline installer contract checks; never use the real home directory."""
import hashlib
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
        helper = self.base / "open-iphone-use-headless"
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        bundle = self.base / "helper.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(helper, arcname=helper.name)
        self.checksum = Path(str(bundle) + ".sha256")
        self.checksum.write_text(hashlib.sha256(bundle.read_bytes()).hexdigest() + "  helper.tar.gz\n")
        self.home = self.base / "home"
        self.env = dict(os.environ, HOME=str(self.home), OIU_INSTALL_ROOT=str(self.home / "runtime"),
                        OIU_BIN_DIR=str(self.home / "bin"), OIU_UNAME="Darwin",
                        OIU_ARCHIVE_URL=source.as_uri(), OIU_HELPER_URL=bundle.as_uri())
        tools = self.base / "tools"
        tools.mkdir()
        lipo = tools / "lipo"
        lipo.write_text('#!/bin/sh\nuname -m\n')
        lipo.chmod(0o755)
        self.env["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]

    def run_install(self, *args):
        return subprocess.run(["bash", str(ROOT / "install.sh"), *args], env=self.env,
                              text=True, capture_output=True)

    def test_dry_run_has_plan_and_no_writes(self):
        result = self.run_install("--version", "vTEST", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("vTEST", self.env["OIU_ARCHIVE_URL"], self.env["OIU_HELPER_URL"],
                         str(self.home / "runtime"), str(self.home / "bin"), "Prebuilt helper"):
            self.assertIn(expected, result.stdout)
        self.assertFalse(self.home.exists())

    def test_checksum_is_verified(self):
        result = self.run_install("--version", "vTEST", "--verify-downloads")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.checksum.write_text("0" * 64 + "  helper.tar.gz\n")
        result = self.run_install("--version", "vTEST", "--verify-downloads")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(self.home.exists())

    def test_offline_uninstall_delegates_to_managed_setup(self):
        runtime = self.home / "runtime"
        runtime.mkdir(parents=True)
        (runtime / ".open-iphone-use-managed").touch()
        (runtime / "setup.sh").write_text('#!/bin/sh\nprintf "delegated %s\\n" "$1"\n')
        result = self.run_install("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delegated --uninstall", result.stdout)

    def test_architecture_check_without_lipo(self):
        tools = Path(self.env["PATH"].split(os.pathsep)[0])
        (tools / "lipo").write_text('#!/bin/sh\nexit 1\n')
        file_command = tools / "file"
        file_command.write_text('#!/bin/sh\nprintf "Mach-O executable %s\\n" "$(uname -m)"\n')
        file_command.chmod(0o755)
        result = self.run_install("--version", "vTEST", "--verify-downloads")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incompatible_helper_fails_verification(self):
        lipo = Path(self.env["PATH"].split(os.pathsep)[0]) / "lipo"
        lipo.write_text('#!/bin/sh\necho no-such-architecture\n')
        result = self.run_install("--version", "vTEST", "--verify-downloads")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("host architecture", result.stderr)

    def test_unsupported_platforms(self):
        for os_name, message in (("MINGW64_NT", "PowerShell"), ("Plan9", "Unsupported OS")):
            with self.subTest(os=os_name):
                self.env["OIU_UNAME"] = os_name
                result = self.run_install("--version", "vTEST", "--dry-run")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
