"""Build and verify ``enocean_custom.zip``, the HACS release asset.

``hacs.json`` declares ``"zip_release": true`` and
``"filename": "enocean_custom.zip"``. HACS then downloads
``https://github.com/<repo>/releases/download/<tag>/enocean_custom.zip`` and
extracts it with ``ZipFile.extractall(<config>/custom_components/enocean_custom)``
(HACS 2.0.5, ``custom_components/hacs/repositories/base.py`` lines 559-603).
The archive must therefore hold the *content* of
``custom_components/enocean_custom/`` at its root: ``manifest.json`` on the
first level, no ``custom_components/enocean_custom/`` directory inside. A
nested archive downloads and extracts without any HACS error and leaves Home
Assistant with an integration directory that has no manifest. This script
exists to make that mistake impossible to ship.

Modes::

    python scripts/build_release_zip.py [--output dist/enocean_custom.zip] [--tag vX.Y.Z]
    python scripts/build_release_zip.py --check ZIP [--tag vX.Y.Z | --version X.Y.Z]
                                                    [--install-to DIR]

**Build** writes a deterministic archive: entries sorted by path, one fixed
timestamp (1980-01-01, the earliest a zip entry can carry), one normalized
Unix mode (0644), no directory entries, no compression (``ZIP_STORED``).
Stored entries are what makes two builds byte-identical on any machine:
deflate output depends on the zlib build behind the interpreter, and the
release workflow compares the attached asset with a fresh rebuild by sha256.
The integration weighs about 1.2 MB, so compression would buy nothing that
matters. ``__pycache__`` directories, ``*.pyc``/``*.pyo`` files and hidden
files or directories (leading dot) are never included.

**Check** opens an existing archive and fails unless all of this holds: the
file is a zip whose CRCs verify; no entry is absolute, contains ``..`` or a
backslash, is a directory entry or a symlink; ``manifest.json`` sits at the
root; its ``version`` equals the expected one (``--version``, or ``--tag``
with its leading ``v`` removed) and the source tree's own manifest version;
the set of files is exactly the set the build would produce from the source
tree; every file's sha256 equals the source file's; every entry is in the
canonical build form described above. ``--install-to DIR`` then simulates the
HACS installation: the archive is extracted with the same ``extractall`` call
into ``DIR/custom_components/<domain>``, the extracted tree is compared to the
archive file by file and compiled with ``compileall``.

Exit status: 0 on success, 1 when the build is refused or a check fails,
2 on usage errors.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "custom_components" / "enocean_custom"
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "enocean_custom.zip"
MANIFEST = "manifest.json"

# Canonical entry form. Changing any of these changes every archive's sha256.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644
CREATE_SYSTEM_UNIX = 3
EXCLUDED_DIRECTORIES = frozenset({"__pycache__"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})

_DRIVE_LETTER = re.compile(r"\A[A-Za-z]:")
_TAG_PREFIX = re.compile(r"\A[vV]")


@dataclass(frozen=True)
class ArchiveSummary:
    """What a build or a check reports about an archive."""

    path: Path
    names: tuple[str, ...]
    sha256: str
    size: int


def is_excluded(relative: PurePosixPath) -> bool:
    """Return True for caches, byte-code and hidden paths (any component)."""
    parts = relative.parts
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in EXCLUDED_DIRECTORIES for part in parts[:-1]):
        return True
    return relative.suffix in EXCLUDED_SUFFIXES


def source_files(source: Path) -> dict[str, Path]:
    """Map every archive name to its source file, sorted by name."""
    if not source.is_dir():
        raise FileNotFoundError(f"source directory not found: {source}")
    files: dict[str, Path] = {}
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = PurePosixPath(path.relative_to(source).as_posix())
        if is_excluded(relative):
            continue
        files[str(relative)] = path
    if MANIFEST not in files:
        raise FileNotFoundError(f"{source} has no {MANIFEST}; not an integration")
    return dict(sorted(files.items()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ManifestError(ValueError):
    """The manifest of an archive or source tree is unusable."""


def _manifest_field(data: bytes, origin: str, field: str) -> str:
    """Return a non-empty string field of a manifest, or raise ManifestError."""
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exception:
        raise ManifestError(f"{origin}: {MANIFEST} is not valid JSON: {exception}")
    value = manifest.get(field) if type(manifest) is dict else None
    if type(value) is not str or not value:
        raise ManifestError(f"{origin}: {MANIFEST} has no string '{field}'")
    return value


def manifest_version(data: bytes, origin: str) -> str:
    """Return the ``version`` declared by a manifest."""
    return _manifest_field(data, origin, "version")


def manifest_domain(data: bytes, origin: str) -> str:
    """Return the ``domain`` declared by a manifest (HACS's target directory)."""
    domain = _manifest_field(data, origin, "domain")
    if not re.fullmatch(r"[a-z0-9_]+", domain):
        raise ManifestError(f"{origin}: {MANIFEST} 'domain' {domain!r} is not valid")
    return domain


def version_from_tag(tag: str) -> str:
    """``v2.11.1`` -> ``2.11.1``; a tag without the prefix is used as is."""
    return _TAG_PREFIX.sub("", tag.strip(), count=1)


def build_archive(source: Path, output: Path) -> ArchiveSummary:
    """Write the canonical archive of ``source`` to ``output`` atomically."""
    files = source_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".part", dir=output.parent
    )
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as archive:
            for name, path in files.items():
                info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = CREATE_SYSTEM_UNIX
                info.external_attr = FILE_MODE << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary_path, output)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return ArchiveSummary(
        path=output,
        names=tuple(files),
        sha256=sha256_file(output),
        size=output.stat().st_size,
    )


def entry_problem(info: zipfile.ZipInfo) -> str | None:
    """Return why an entry must be rejected, or None when its path is safe."""
    name = info.filename
    if not name or "\x00" in name:
        return "empty or NUL-containing name"
    if "\\" in name:
        return "backslash in name"
    if name.startswith("/") or _DRIVE_LETTER.match(name):
        return "absolute path"
    if info.is_dir():
        return "directory entry (the build never writes any)"
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "'..', '.' or empty path component"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "symbolic link"
    return None


def canonical_form_problem(info: zipfile.ZipInfo) -> str | None:
    """Return why an entry is not in the deterministic build form."""
    if info.date_time != FIXED_DATE_TIME:
        return f"timestamp {info.date_time} instead of {FIXED_DATE_TIME}"
    if info.compress_type != zipfile.ZIP_STORED:
        return "compressed entry (the build stores entries uncompressed)"
    if info.external_attr >> 16 != FILE_MODE:
        return f"mode {oct(info.external_attr >> 16)} instead of {oct(FILE_MODE)}"
    if info.create_system != CREATE_SYSTEM_UNIX:
        return f"create_system {info.create_system} instead of {CREATE_SYSTEM_UNIX}"
    return None


def check_archive(
    zip_path: Path, source: Path, expected_version: str | None
) -> tuple[list[str], ArchiveSummary | None]:
    """Verify ``zip_path`` against ``source``; return (errors, summary)."""
    errors: list[str] = []
    if not zip_path.is_file():
        return [f"{zip_path}: no such file"], None
    if not zipfile.is_zipfile(zip_path):
        return [f"{zip_path}: not a zip file"], None

    with zipfile.ZipFile(zip_path) as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            return [f"{zip_path}: CRC error on entry {corrupt!r}"], None

        infos = archive.infolist()
        names = [info.filename for info in infos]
        for info in infos:
            problem = entry_problem(info)
            if problem is not None:
                errors.append(f"rejected entry {info.filename!r}: {problem}")
        if errors:
            return errors, None

        if len(set(names)) != len(names):
            errors.append("duplicate entry names")
        if names != sorted(names):
            errors.append("entries are not sorted by name (not built by this script)")
        for info in infos:
            problem = canonical_form_problem(info)
            if problem is not None:
                errors.append(
                    f"entry {info.filename!r} not in canonical form: {problem}"
                )

        if MANIFEST not in names:
            nested = [name for name in names if name.endswith(f"/{MANIFEST}")]
            hint = (
                f"; found {nested[0]!r}: the archive is nested, HACS extracts it "
                "directly into custom_components/<domain>/"
                if nested
                else ""
            )
            errors.append(f"{MANIFEST} is not at the root of the archive{hint}")
            return errors, None

        try:
            archive_version = manifest_version(archive.read(MANIFEST), str(zip_path))
            expected = source_files(source)
            source_version = manifest_version(
                expected[MANIFEST].read_bytes(), str(source)
            )
        except (FileNotFoundError, ManifestError) as exception:
            errors.append(str(exception))
            return errors, None

        if expected_version is not None and archive_version != expected_version:
            errors.append(
                f"archive {MANIFEST} version is {archive_version}, "
                f"expected {expected_version}"
            )
        if archive_version != source_version:
            errors.append(
                f"archive {MANIFEST} version is {archive_version} but the source tree "
                f"is at {source_version}"
            )

        missing = sorted(set(expected) - set(names))
        extra = sorted(set(names) - set(expected))
        for name in missing:
            errors.append(f"missing from the archive: {name}")
        for name in extra:
            errors.append(f"not part of the source tree: {name}")
        for name, path in expected.items():
            if name in names and sha256_bytes(archive.read(name)) != sha256_file(path):
                errors.append(f"content differs from the source tree: {name}")

    summary = ArchiveSummary(
        path=zip_path,
        names=tuple(names),
        sha256=sha256_file(zip_path),
        size=zip_path.stat().st_size,
    )
    return errors, summary


def simulate_install(zip_path: Path, install_root: Path) -> tuple[list[str], Path]:
    """Extract exactly like HACS does, then verify and compile the result."""
    errors: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        try:
            domain = manifest_domain(archive.read(MANIFEST), str(zip_path))
        except (KeyError, ManifestError) as exception:
            return [f"cannot simulate the installation: {exception}"], install_root
        target = install_root / "custom_components" / domain
        if target.exists() and any(target.iterdir()):
            return [f"refusing to extract into the non-empty {target}"], target
        # HACS 2.0.5, repositories/base.py: zip_file.extractall(self.content.path.local)
        archive.extractall(target)
        expected = {info.filename: info for info in archive.infolist()}
        for name, info in expected.items():
            extracted = target / name
            if not extracted.is_file():
                errors.append(f"not extracted: {name}")
            elif sha256_file(extracted) != sha256_bytes(archive.read(info)):
                errors.append(f"extracted content differs: {name}")

    on_disk = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    }
    for name in sorted(on_disk - set(expected)):
        errors.append(f"unexpected file after extraction: {name}")
    if not (target / MANIFEST).is_file():
        errors.append(f"{target / MANIFEST} is missing after extraction")
    if not compileall.compile_dir(str(target), quiet=1, force=True):
        errors.append(f"{target}: byte-compilation failed")
    return errors, target


def print_summary(summary: ArchiveSummary, archive_sizes: dict[str, int]) -> None:
    print(f"{summary.path}: {len(summary.names)} files, {summary.size} bytes")
    for name in summary.names:
        print(f"  {name}  ({archive_sizes[name]} bytes)")
    print(f"sha256  {summary.sha256}  {summary.path}")


def entry_sizes(zip_path: Path) -> dict[str, int]:
    with zipfile.ZipFile(zip_path) as archive:
        return {info.filename: info.file_size for info in archive.infolist()}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_release_zip.py",
        description=(
            "Build the deterministic HACS release asset (content of the "
            "integration directory at the archive root), or verify one."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"integration directory to package (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"archive to write in build mode (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check",
        metavar="ZIP",
        type=Path,
        help="verify this existing archive instead of building one",
    )
    expected = parser.add_mutually_exclusive_group()
    expected.add_argument(
        "--version", help="version the manifest must declare, e.g. 2.11.1"
    )
    expected.add_argument(
        "--tag", help="release tag the manifest must match, e.g. v2.11.1"
    )
    parser.add_argument(
        "--install-to",
        metavar="DIR",
        type=Path,
        help=(
            "with --check: extract like HACS into DIR/custom_components/<domain>, "
            "verify the extracted tree and byte-compile it"
        ),
    )
    args = parser.parse_args(argv)
    if args.install_to is not None and args.check is None:
        parser.error("--install-to requires --check")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expected_version = (
        version_from_tag(args.tag) if args.tag else args.version
    )  # None when neither is given

    if args.check is None:
        try:
            files = source_files(args.source)
            source_version = manifest_version(
                files[MANIFEST].read_bytes(), str(args.source)
            )
        except (FileNotFoundError, ManifestError) as exception:
            print(f"ERROR: {exception}", file=sys.stderr)
            return 1
        if expected_version is not None and source_version != expected_version:
            print(
                f"ERROR: refusing to build: {args.source / MANIFEST} is at version "
                f"{source_version}, expected {expected_version}",
                file=sys.stderr,
            )
            return 1
        summary = build_archive(args.source, args.output)
        print_summary(summary, entry_sizes(summary.path))
        print(f"RELEASE_ZIP_BUILT version={source_version} files={len(summary.names)}")
        return 0

    errors, summary = check_archive(args.check, args.source, expected_version)
    if summary is not None:
        print_summary(summary, entry_sizes(summary.path))
    if not errors and args.install_to is not None:
        errors, target = simulate_install(args.check, args.install_to)
        if not errors:
            print(f"HACS_INSTALL_SIMULATION_OK target={target}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"RELEASE_ZIP_CHECK_FAILED errors={len(errors)}", file=sys.stderr)
        return 1
    print(f"RELEASE_ZIP_CHECK_OK files={len(summary.names)} sha256={summary.sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
