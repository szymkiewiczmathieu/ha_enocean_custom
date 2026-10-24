"""The HACS release asset is built deterministically and verified strictly.

``hacs.json`` tells HACS to install every release from the ``enocean_custom.zip``
asset, extracted straight into ``custom_components/enocean_custom/``. These
tests pin the contract of ``scripts/build_release_zip.py``: the content of the
integration directory at the archive root, caches and hidden files left out,
byte-identical output from one build to the next, and a ``--check`` that
rejects anything HACS would install wrongly (nested directory, path traversal,
foreign, missing or modified files, version mismatch with the manifest).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_zip as release_zip

FAKE_MANIFEST = {
    "domain": "fake_domain",
    "name": "Fake",
    "codeowners": [],
    "version": "1.2.3",
}


def _populate_fake_source(source: Path) -> dict[str, bytes]:
    """Create a small integration tree; return the files the archive must hold."""
    expected = {
        "__init__.py": b"DOMAIN = 'fake_domain'\n",
        "manifest.json": json.dumps(FAKE_MANIFEST, indent=2).encode() + b"\n",
        "data/products.json": b"{}\n",
        "translations/en.json": b"{}\n",
        "zz_last.py": b"",
    }
    for name, content in expected.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # Never packaged: byte-code caches, stray byte-code, hidden files and dirs.
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "__init__.cpython-314.pyc").write_bytes(b"\x00")
    (source / "data" / "__pycache__").mkdir()
    (source / "data" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\x00")
    (source / "stray.pyc").write_bytes(b"\x00")
    (source / "stray.pyo").write_bytes(b"\x00")
    (source / ".DS_Store").write_bytes(b"\x00")
    (source / ".hidden_dir").mkdir()
    (source / ".hidden_dir" / "secret.py").write_bytes(b"")
    return expected


def _canonical_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=release_zip.FIXED_DATE_TIME)
    info.filename = name  # keep the raw name, ZipInfo may normalize it
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = release_zip.CREATE_SYSTEM_UNIX
    info.external_attr = release_zip.FILE_MODE << 16
    return info


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    """Write a zip by hand, in canonical form, in the given order."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries.items():
            archive.writestr(_canonical_info(name), content)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process and capture its exit code and streams."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = release_zip.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class ExclusionTests(unittest.TestCase):
    def test_caches_bytecode_and_hidden_paths_are_excluded(self):
        for name in (
            "__pycache__/x.cpython-314.pyc",
            "sub/__pycache__/x.pyc",
            "stray.pyc",
            "stray.pyo",
            ".DS_Store",
            ".hidden/secret.py",
            "sub/.hidden/secret.py",
        ):
            self.assertTrue(release_zip.is_excluded(PurePosixPath(name)), name)
        for name in ("manifest.json", "brand/icon@2x.png", "a/b/c.py", "x.pyi"):
            self.assertFalse(release_zip.is_excluded(PurePosixPath(name)), name)

    def test_tag_prefix_is_stripped_once(self):
        self.assertEqual(release_zip.version_from_tag("v2.11.1"), "2.11.1")
        self.assertEqual(release_zip.version_from_tag("V2.11.1"), "2.11.1")
        self.assertEqual(release_zip.version_from_tag("2.11.1"), "2.11.1")
        self.assertEqual(release_zip.version_from_tag(" v2.11.1\n"), "2.11.1")


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release-zip-build-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = self.tmp / "src" / "fake_domain"
        self.source.mkdir(parents=True)
        self.expected = _populate_fake_source(self.source)

    def test_content_sits_at_the_archive_root_without_caches(self):
        summary = release_zip.build_archive(self.source, self.tmp / "out" / "fake.zip")

        self.assertEqual(list(summary.names), sorted(self.expected))
        self.assertIn("manifest.json", summary.names)
        self.assertNotIn("custom_components/fake_domain/manifest.json", summary.names)
        with zipfile.ZipFile(summary.path) as archive:
            infos = archive.infolist()
            self.assertEqual([info.filename for info in infos], sorted(self.expected))
            for info in infos:
                self.assertEqual(archive.read(info), self.expected[info.filename])
                self.assertEqual(info.date_time, release_zip.FIXED_DATE_TIME)
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.external_attr >> 16, 0o100644)
                self.assertEqual(info.create_system, 3)
                self.assertFalse(info.is_dir())
        self.assertEqual(summary.size, summary.path.stat().st_size)

    def test_two_builds_are_byte_identical(self):
        first = release_zip.build_archive(self.source, self.tmp / "a.zip")

        # Different mtimes, permissions, location and creation order of the
        # source files must not leak into the archive.
        for path in self.source.rglob("*"):
            if path.is_file():
                os.utime(path, (0, 0))
                path.chmod(0o755)
        moved = self.tmp / "elsewhere" / "renamed_dir"
        shutil.copytree(self.source, moved)
        for name in sorted(self.expected, reverse=True):
            (moved / name).write_bytes(self.expected[name])
        second = release_zip.build_archive(moved, self.tmp / "b.zip")

        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(
            (self.tmp / "a.zip").read_bytes(), (self.tmp / "b.zip").read_bytes()
        )

    def test_build_refuses_a_tree_whose_version_differs_from_the_tag(self):
        output = self.tmp / "never.zip"
        code, _, err = _run(
            ["--source", str(self.source), "--output", str(output), "--tag", "v9.9.9"]
        )
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())
        self.assertIn("refusing to build", err)
        self.assertIn("expected 9.9.9", err)
        self.assertEqual(list(self.tmp.glob("*.part")), [])

    def test_build_accepts_a_matching_tag_or_version(self):
        for flags in (["--tag", "v1.2.3"], ["--tag", "1.2.3"], ["--version", "1.2.3"]):
            output = self.tmp / "ok.zip"
            code, out, err = _run(
                ["--source", str(self.source), "--output", str(output), *flags]
            )
            self.assertEqual(code, 0, err)
            self.assertIn("RELEASE_ZIP_BUILT version=1.2.3 files=5", out)
            self.assertRegex(out, r"sha256  [0-9a-f]{64}  ")
            self.assertIn("  manifest.json  (", out)

    def test_build_rejects_a_directory_without_manifest(self):
        (self.source / "manifest.json").unlink()
        code, _, err = _run(
            ["--source", str(self.source), "--output", str(self.tmp / "x.zip")]
        )
        self.assertEqual(code, 1)
        self.assertIn("has no manifest.json", err)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release-zip-check-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = self.tmp / "src" / "fake_domain"
        self.source.mkdir(parents=True)
        self.expected = _populate_fake_source(self.source)
        self.good = self.tmp / "good.zip"
        release_zip.build_archive(self.source, self.good)

    def _check(self, zip_path: Path, *extra: str) -> tuple[int, str, str]:
        return _run(["--check", str(zip_path), "--source", str(self.source), *extra])

    def test_accepts_the_built_archive(self):
        for extra in ((), ("--version", "1.2.3"), ("--tag", "v1.2.3")):
            code, out, err = self._check(self.good, *extra)
            self.assertEqual(code, 0, err)
            self.assertEqual(err, "")
            self.assertIn("RELEASE_ZIP_CHECK_OK files=5 sha256=", out)
            self.assertIn("  manifest.json  (", out)

    def test_rejects_a_nested_archive(self):
        nested = self.tmp / "nested.zip"
        _write_zip(
            nested,
            {
                f"custom_components/fake_domain/{name}": content
                for name, content in sorted(self.expected.items())
            },
        )
        code, _, err = self._check(nested)
        self.assertEqual(code, 1)
        self.assertIn("manifest.json is not at the root", err)
        self.assertIn("nested", err)

    def test_rejects_traversal_absolute_directory_and_backslash_entries(self):
        cases = (
            ("../evil.py", "'..'"),
            ("sub/../evil.py", "'..'"),
            ("/etc/passwd", "absolute path"),
            ("C:/evil.py", "absolute path"),
            ("dir/", "directory entry"),
            ("a\\b.py", "backslash"),
        )
        for name, reason in cases:
            path = self.tmp / "bad.zip"
            _write_zip(path, {**self.expected, name: b""})
            code, _, err = self._check(path)
            self.assertEqual(code, 1, name)
            self.assertIn(f"rejected entry {name!r}", err)
            self.assertIn(reason, err)

    def test_rejects_symlink_entries(self):
        path = self.tmp / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in sorted(self.expected.items()):
                archive.writestr(_canonical_info(name), content)
            link = _canonical_info("link.py")
            link.external_attr = 0o120777 << 16
            archive.writestr(link, b"manifest.json")
        code, _, err = self._check(path)
        self.assertEqual(code, 1)
        self.assertIn("rejected entry 'link.py': symbolic link", err)

    def test_rejects_a_version_mismatch(self):
        for flags in (("--version", "9.9.9"), ("--tag", "v9.9.9")):
            code, _, err = self._check(self.good, *flags)
            self.assertEqual(code, 1)
            self.assertIn("version is 1.2.3, expected 9.9.9", err)

        # The archive must also match the manifest of the tree it is checked
        # against: an archive built before a version bump is caught.
        bumped = {**FAKE_MANIFEST, "version": "1.2.4"}
        (self.source / "manifest.json").write_text(json.dumps(bumped), "utf-8")
        code, _, err = self._check(self.good, "--version", "1.2.3")
        self.assertEqual(code, 1)
        self.assertIn("source tree is at 1.2.4", err)
        self.assertIn("content differs from the source tree: manifest.json", err)

    def test_rejects_extra_missing_and_modified_files(self):
        extra = self.tmp / "extra.zip"
        _write_zip(extra, dict(sorted({**self.expected, "stray.txt": b"x"}.items())))
        code, _, err = self._check(extra)
        self.assertEqual(code, 1)
        self.assertIn("not part of the source tree: stray.txt", err)

        missing = self.tmp / "missing.zip"
        _write_zip(
            missing,
            {k: v for k, v in sorted(self.expected.items()) if k != "zz_last.py"},
        )
        code, _, err = self._check(missing)
        self.assertEqual(code, 1)
        self.assertIn("missing from the archive: zz_last.py", err)

        modified = self.tmp / "modified.zip"
        _write_zip(
            modified,
            dict(sorted({**self.expected, "__init__.py": b"tampered\n"}.items())),
        )
        code, _, err = self._check(modified)
        self.assertEqual(code, 1)
        self.assertIn("content differs from the source tree: __init__.py", err)

    def test_rejects_archives_not_in_canonical_form(self):
        path = self.tmp / "handmade.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(self.expected.items()):
                archive.writestr(name, content)  # now(), deflated, mode 0600
        code, _, err = self._check(path)
        self.assertEqual(code, 1)
        self.assertIn("not in canonical form", err)

        unsorted = self.tmp / "unsorted.zip"
        _write_zip(unsorted, dict(sorted(self.expected.items(), reverse=True)))
        code, _, err = self._check(unsorted)
        self.assertEqual(code, 1)
        self.assertIn("not sorted", err)

    def test_rejects_non_zip_and_missing_files(self):
        text = self.tmp / "text.zip"
        text.write_bytes(b"hello")
        code, _, err = self._check(text)
        self.assertEqual(code, 1)
        self.assertIn("not a zip file", err)
        code, _, err = self._check(self.tmp / "absent.zip")
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)

    def test_install_simulation_extracts_like_hacs_and_compiles(self):
        config = self.tmp / "config"
        code, out, err = self._check(self.good, "--install-to", str(config))
        self.assertEqual(code, 0, err)
        target = config / "custom_components" / "fake_domain"
        self.assertTrue((target / "manifest.json").is_file())
        self.assertTrue((target / "translations" / "en.json").is_file())
        self.assertFalse((target / "custom_components").exists())
        self.assertEqual(
            json.loads((target / "manifest.json").read_text("utf-8"))["version"],
            "1.2.3",
        )
        self.assertTrue(list((target / "__pycache__").glob("__init__.*.pyc")))
        self.assertIn(f"HACS_INSTALL_SIMULATION_OK target={target}", out)

        # A second extraction into the now populated directory is refused.
        code, _, err = self._check(self.good, "--install-to", str(config))
        self.assertEqual(code, 1)
        self.assertIn("non-empty", err)

    def test_install_simulation_reports_files_that_do_not_compile(self):
        (self.source / "__init__.py").write_bytes(b"def broken(:\n")
        bad = self.tmp / "bad.zip"
        release_zip.build_archive(self.source, bad)
        code, _, err = self._check(bad, "--install-to", str(self.tmp / "config"))
        self.assertEqual(code, 1)
        self.assertIn("byte-compilation failed", err)

    def test_install_simulation_requires_check(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            release_zip.main(["--install-to", str(self.tmp)])
        self.assertEqual(raised.exception.code, 2)


class RealTreeTests(unittest.TestCase):
    """The shipped integration packages, checks and installs from its archive."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="release-zip-real-"))
        cls.archive = cls.tmp / "enocean_custom.zip"
        cls.summary = release_zip.build_archive(release_zip.DEFAULT_SOURCE, cls.archive)
        cls.manifest = json.loads(
            (release_zip.DEFAULT_SOURCE / "manifest.json").read_text("utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hacs_json_declares_the_asset_this_script_builds(self):
        hacs = json.loads((ROOT / "hacs.json").read_text("utf-8"))
        self.assertIs(hacs.get("zip_release"), True)
        self.assertEqual(hacs.get("filename"), release_zip.DEFAULT_OUTPUT.name)
        # HACS 2.0.5 only tries the release asset when the name ends in .zip
        # (repositories/base.py, should_try_releases).
        self.assertTrue(hacs["filename"].endswith(".zip"))
        self.assertEqual(
            release_zip.DEFAULT_OUTPUT, ROOT / "dist" / "enocean_custom.zip"
        )

    def test_archive_matches_the_manifest_version_and_installs(self):
        names = self.summary.names
        for required in (
            "manifest.json",
            "__init__.py",
            "config_flow.py",
            "strings.json",
            "translations/en.json",
            "brand/icon.png",
            "enocean_library/protocol/EEP.xml",
        ):
            self.assertIn(required, names)
        self.assertFalse(any(name.startswith("custom_components/") for name in names))
        self.assertFalse(
            any(
                "__pycache__" in name or name.endswith((".pyc", ".pyo"))
                for name in names
            )
        )
        self.assertFalse(
            any(part.startswith(".") for name in names for part in name.split("/"))
        )

        config = self.tmp / "config"
        code, out, err = _run(
            [
                "--check",
                str(self.archive),
                "--version",
                self.manifest["version"],
                "--install-to",
                str(config),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"RELEASE_ZIP_CHECK_OK files={len(names)}", out)
        installed = json.loads(
            (
                config / "custom_components" / "enocean_custom" / "manifest.json"
            ).read_text("utf-8")
        )
        self.assertEqual(installed, self.manifest)
        self.assertEqual(installed["domain"], "enocean_custom")

    def test_archive_version_follows_the_manifest(self):
        code, _, err = _run(["--check", str(self.archive), "--tag", "v0.0.0"])
        self.assertEqual(code, 1)
        self.assertIn(f"version is {self.manifest['version']}, expected 0.0.0", err)


if __name__ == "__main__":
    unittest.main()
