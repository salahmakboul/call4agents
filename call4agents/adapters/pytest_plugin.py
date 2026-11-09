# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""pytest plugin (experimental): `call4agents crashtest` inside your test suite.

    def test_refund_survives_a_crash(c4a_crashtest):
        c4a_crashtest("agents/refund_agent.py", watch=["refund"], test_accounts=True)

The fixture runs the script once cleanly, then kills it before and after each action and each save, restarts it,
and fails the test if an action ran twice or a call ran that the clean run never made. It returns the report.

Every action really runs, many times. The fixture refuses to start until you pass test_accounts=True, which says
the script talks only to fakes, sandboxes or test accounts. Installed with call4agents; pytest loads it by itself.
"""
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

import pytest

from call4agents.crashtest import crashtest, problems


def _explain(report: Dict[str, Any], found: Dict[str, Dict[str, list]]) -> str:
    lines = [f"call4agents crashtest: {Path(report['script']).name}, {report['tested']} crash(es) tested"]
    for call, where in found["twice"].items():
        lines.append(f"  RAN TWICE     {call}  (in {len(where)} crashes, first when killed {where[0]})")
    for call, where in found["unapproved"].items():
        lines.append(f"  NOT APPROVED  {call}  (the clean run never made this call; first when killed {where[0]})")
    lines.append("The one-line fix:\n" + report["fix"])
    return "\n".join(lines)


@pytest.fixture
def c4a_crashtest() -> Callable[..., Dict[str, Any]]:
    def run(script: str, *, watch: Iterable[str] = (), args: Iterable[str] = (), max_crashes: int = 200,
            timeout: float = 120, test_accounts: bool = False) -> Dict[str, Any]:
        if test_accounts is not True:
            pytest.fail("c4a_crashtest runs every action of the script many times, for real. Point the script at fakes, "
                        "sandboxes or test accounts, then pass test_accounts=True.", pytrace=False)
        if not Path(script).is_file():
            pytest.fail(f"c4a_crashtest: no script at {script}", pytrace=False)
        report = crashtest(str(script), list(watch), list(args), max_crashes, timeout)
        if "error" in report:
            pytest.fail(f"c4a_crashtest stopped: {report['error']}\n{report.get('stderr', '')}", pytrace=False)
        found = problems(report)
        if found["twice"] or found["unapproved"]:
            pytest.fail(_explain(report, found), pytrace=False)
        return report
    return run
