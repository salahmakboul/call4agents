Crash test leaderboard: the saved runs of 19 November 2026
==========================================================

Page: https://salahmakboul.github.io/call4agents/leaderboard/

The test. The agent asks to call pay(amount=10, to="alice"), a tool that needs a person's approval. The run pauses
and the framework saves its state. A person says yes. The tool pays (a line in a local file) and the process is
killed with os._exit right after, before the framework saves anything else. A new process resumes the way the
framework documents. Then the payments in the file are counted. A control run does the same with no crash.

Fake models only. No API key, no network, no real money. Each phase is a separate process; each run is a new temp
folder. Python 3.12.10, Windows 10, one machine. Runs made 18 Nov 2026 23:55 UTC to 19 Nov about 01:30 UTC
(started_utc in each file).

What is here
------------
<framework>/crash_NN.json       one file per crash run (the "payments" field is the count)
<framework>/control_NN.json     one file per control run
<framework>/edited_NN.json      and other names: extra cases, not in the leaderboard table
<framework>_guarded/...         the same agent with call4agents 0.7.3 (from PyPI) on the tool
scripts/                        the agent scripts that were run (three_frameworks.py: LangGraph, Pydantic AI, OpenAI
                                Agents SDK; adk_confirm_crash.py: Google ADK; maf_approve_crash.py: Agent Framework)
scripts_guarded/                the same scripts with the guard; c4a_guard.py is the guard, DIFF.txt every change
pip_freeze/, pip_freeze_guarded/  the exact packages in each Python environment
tally.py, tally_guarded.py      count the payments from the run files only
tally.txt, tally_guarded.txt    their output on these files
rerun.py, rerun_guarded.py      the drivers that made the run files (one call = n runs of one case)
rerun_row.py                    rerun one row with one command (see below)

Two changes from the drivers used on 19 Nov, both in rerun.py and rerun_guarded.py: entries for frameworks that are
not on the page are removed, and a new run file gets today's date (the 19 Nov drivers wrote a fixed "2026-11-19").
tally.py has the matching entry removed and skips the *_guarded folders (on 19 Nov it ran before they existed;
tally_guarded.py counts them). The scripts are unchanged copies. In the run files and freezes the start of every
absolute local path (drive, Users folder and user name) is replaced by <home>; nothing else in them changes.

Rerun one row (a run took 4 to 28 s on our machine)
---------------------------------------------------
python -m venv .venv            Windows: .venv\Scripts\activate   Linux/macOS: source .venv/bin/activate
pip install <the row's packages, below> call4agents==0.7.3
python rerun_row.py langgraph --n 30 --guarded

  langgraph                    pip install "langgraph==1.2.22" "langgraph-checkpoint-sqlite==3.1.3"
  pydantic_ai                  pip install "pydantic-ai-slim==2.70.0"
  openai_agents                pip install "openai-agents==0.25.0"
  google_adk                   pip install "google-adk==2.14.1"
  microsoft_agent_framework    pip install "agent-framework-core==1.26.0"

Leave the versions out to test the newest releases. One Python environment per row, as on 19 Nov.
rerun_row.py copies the drivers into a new folder my-rerun-<row>-<time>/, runs the cases there and prints the count.

Correct a row
-------------
Open an issue or a pull request on https://github.com/salahmakboul/call4agents with the my-rerun-... folder (the
run files unchanged), your pip freeze, and one line on what you changed and why, ideally with a link to the
framework's docs. A different configuration (a durability mode, a retry policy, a durable-execution add-on) is
welcome as an extra row next to the default one.
