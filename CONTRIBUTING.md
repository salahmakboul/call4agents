# Contributing

Thank you for helping. Questions, bug reports, measurements and pull requests are all welcome.

## Where to start

- **A question or an idea:** open a [discussion](https://github.com/salahmakboul/call4agents/discussions).
- **Something does not work:** open an [issue](https://github.com/salahmakboul/call4agents/issues/new/choose).
  `call4agents report` prints your versions and a link with them filled in (it sends nothing).
- **A security problem:** please report it privately, see [SECURITY.md](SECURITY.md).

We try to answer every issue and discussion within a few days.

## Making a change

```bash
git clone https://github.com/salahmakboul/call4agents && cd call4agents
python -m pip install -e .
python -m tests.test_c4a_kit            # the core checks, no network, no API keys
python -m tests.test_c4a_production
python -m tests.test_idempotency
python -m tests.test_undo
python -m tests.test_tickets
python -m tests.test_crash_matrix      # kills a real process at every write (several minutes)
```

Optional suites need more:
- `python -m tests.test_adapters` and `python -m tests.test_tickets --require-frameworks` need the
  frameworks: `pip install langgraph langgraph-checkpoint-sqlite langchain pydantic-ai openai-agents mcp`.
- `python -m tests.test_shared_record` needs `pip install "psycopg[binary]" pgserver` (a throwaway local PostgreSQL).

CI runs all of them on Linux, macOS and Windows for every pull request.

## The rules we keep

1. **The core uses only the Python standard library.** Framework code lives in `call4agents/adapters/`,
   other stores in `call4agents/stores/`. A check (K7) fails if the core imports anything else.
2. **Every guarantee has a check that fails when its bug comes back.** If you add or change a guard, add a
   mutation to `tests/mutations/run_kit_mutations.py`: the bug put back, and the suite that must catch it.
   Run `python -m tests.mutations.run_kit_mutations` to see each one caught.
3. **Every claim is measured.** The README says what is proven and what is not. If you add a number, add the
   command that reproduces it. New features are marked experimental until real users have used them.
4. **No secrets or personal data** in code, tests or examples. `python -m tests.secret_scan` must pass. In tests,
   use obviously fake values such as `b"t" * 16`.
5. **Plain words.** Error messages say what happened and what to do next.

## Pull requests

Keep a pull request to one change, explain why it is needed, and say how you checked it. The template asks
for the same. Small fixes to docs and examples are very welcome.

By contributing you agree that your contribution is licensed under the Apache License 2.0, like the project.
Please also follow the [code of conduct](CODE_OF_CONDUCT.md).
