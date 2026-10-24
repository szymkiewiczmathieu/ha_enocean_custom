# Releasing

Since 2.11.1 every release carries one asset, `enocean_custom.zip`, and
`hacs.json` declares it:

```json
"zip_release": true,
"filename": "enocean_custom.zip"
```

HACS (2.0.5) and the Home Assistant Marketplace then install and update the
integration from
`https://github.com/szymkiewiczmathieu/ha_enocean_custom/releases/download/<tag>/enocean_custom.zip`
instead of the Git tree, extracting the archive straight into
`<config>/custom_components/enocean_custom/`. GitHub counts each download, and
HACS shows that count in the store. Releases that predate 2.11.1 keep
installing from the Git tree: HACS reads the `hacs.json` of the tag it installs.

## What the archive is

The content of `custom_components/enocean_custom/` at the archive root:
`manifest.json` on the first level, no `custom_components/enocean_custom/`
directory inside, no `__pycache__`, byte-code or hidden files. It is built by
`scripts/build_release_zip.py`, deterministically: sorted entries, one fixed
timestamp, one normalized mode, stored (uncompressed, about 1.2 MB). Two builds
of the same tree are byte-identical on any machine, which is what lets the CI
compare an attached asset with a rebuild by sha256.

The script's `--check` refuses an archive unless `manifest.json` is at the root,
its version matches the tag, every entry has a safe path, the file list equals
the source tree and every file's sha256 equals the source file's.
`--install-to DIR` additionally extracts it the way HACS does and
byte-compiles the result.

## Procedure

Work from a clean checkout of the commit to release, with the version bumped
in `custom_components/enocean_custom/manifest.json` and the CHANGELOG entry
written, after the `Validate` workflow is green on that commit.

1. Tag and push the tag (`vX.Y.Z`, the manifest version with a `v`).
2. Build the archive for the tag:

   ```bash
   python scripts/build_release_zip.py --tag vX.Y.Z
   ```

   This writes `dist/enocean_custom.zip` and prints its file list and sha256.
   It refuses to build if the manifest is not at version X.Y.Z. The archive is
   also produced by the `release-zip` job of the `Validate` workflow (artifact
   `enocean_custom.zip`); both are byte-identical.
3. Create the release **as a draft** for the tag, with the CHANGELOG entry as
   body, and attach `dist/enocean_custom.zip`. Attach nothing else: HACS 2.0.5
   displays the download count of the first asset of the release.
4. Download the attached asset back from the draft and check it:

   ```bash
   python scripts/build_release_zip.py --check ~/Downloads/enocean_custom.zip --tag vX.Y.Z
   sha256sum ~/Downloads/enocean_custom.zip dist/enocean_custom.zip
   ```

   Both lines must pass and the two sha256 must be equal.
5. Publish the release.

The `Release asset` workflow runs on publication. It rebuilds the archive from
the tag, downloads the attached asset and fails if the two differ, or if the
checkout, build or check fails. A green run is the final confirmation.

## If the asset is missing or wrong

HACS downloads `enocean_custom.zip` from the release of the version it
installs. A release published without it, or with an asset under another name,
makes **every** installation and update of that version fail with
"Could not download, see log for details"; HACS restores the previous version
from its backup, so users keep a working integration but see a failed update.
This is why the asset is attached to the draft before publication.

- **Missing asset.** The `Release asset` workflow attaches the archive rebuilt
  from the tag within a few minutes of publication. If it did not run or
  failed, run it by hand: *Actions → Release asset → Run workflow*, tag
  `vX.Y.Z`. It attaches the asset only when none is there; it never replaces
  one. If the repository uses GitHub's immutable releases, assets cannot be
  added after publication at all: publish a new patch release instead.
- **Wrong asset** (the workflow reports a sha256 mismatch): the asset is left
  untouched. Read the diagnostic the workflow prints (`--check` on the attached
  file), rebuild from a clean checkout of the tag, delete the wrong asset,
  upload the new one, then re-run the workflow. If the wrong asset was
  nested (`custom_components/enocean_custom/...` inside), users who updated in
  the meantime have a broken integration directory: publish a corrected patch
  release right away, since HACS will not re-download the same version.
- **Nothing to repair** when the run says the attached asset is byte-identical.

## Verifying in Home Assistant

After publication, in HACS: the integration shows the new version; *Update*
or a fresh install completes, `custom_components/enocean_custom/manifest.json`
carries the new version, and the integration loads after the restart. The
release's asset download count on GitHub increases by one per installation or
update; the HACS store reflects it once the HACS data generator (every four
hours) and the client refresh (every six hours) have run, so up to ten hours
later.
