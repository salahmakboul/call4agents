# Releasing, and rolling back a bad release

## How a release is made

1. All changes are on `main`, and the `tests` workflow is green on Linux, macOS and Windows (kit, frameworks,
   shared record, mutation checks, secret and personal-data scan of the files and of the whole history).
2. The version is raised in `pyproject.toml` and `call4agents/__init__.py`, and `CHANGELOG.md` has a section for it.
3. A signed tag `vX.Y.Z` is pushed. The `release` workflow then:
   - runs the full `tests` workflow again;
   - checks that the tag matches the package version;
   - builds the package and checks what is inside (`tests/check_release.py`: no internal files, no records,
     no secrets);
   - publishes to PyPI with trusted publishing (no API token is stored anywhere);
   - installs the published version from PyPI on clean Linux, macOS and Windows runners and runs
     `call4agents quickstart` and `call4agents doctor`;
   - creates the GitHub release with the notes from `CHANGELOG.md`.

If any step fails, the steps after it do not run.

## If a release is bad

A release is bad if it breaks installs, breaks a guarantee (an action can run twice, an approval can be
reused), or leaks something.

1. **Stop the damage.** Yank the version on PyPI (Manage project → Releases → Yank). `pip install call4agents`
   then skips it, but anyone who pinned it can still install it, so their builds do not break without warning.
   Yanking can be undone. We never delete a version: a deleted version number can never be reused.
2. **Tell people.** Open an issue that says which version, what is wrong, who is affected and what to do.
   For a security problem, publish a GitHub security advisory instead (see [SECURITY.md](../SECURITY.md)).
3. **Fix forward.** Revert or fix the change on `main`, add a check that fails if the bug comes back (and a
   mutation for it), and release a new patch version through the normal steps above. Then close the issue with
   a link to the release.
4. **Run records are safe across versions.** The record format has a version number: a newer kit reads older
   records, and an older kit refuses a record written by a newer format instead of misreading it. Going back to
   an older release is therefore safe unless the record format changed (the changelog says when it does).

## After every release

- Check the PyPI page and `pip install call4agents==X.Y.Z` in a fresh virtual environment.
- Check that the GitHub release shows the signed tag as verified.
