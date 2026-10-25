## What this changes and why

<!-- One change per pull request. Link the issue or discussion if there is one. -->

## How you checked it

<!-- The commands you ran and what they printed. CI runs everything on Linux, macOS and Windows. -->

- [ ] `python -m tests.test_c4a_kit` passes (and the other suites this touches, see CONTRIBUTING.md)
- [ ] A new or changed guarantee has a check, and a mutation in `tests/mutations/run_kit_mutations.py` that the check catches
- [ ] Any new claim in the docs is measured, with the command that reproduces it
- [ ] `python -m tests.secret_scan` passes (no secrets or personal data)
