# `call4agents scan` on real projects

Measured on 2026-10-31, on the public source of 12 open-source agent projects. This checks the scanner. It does not
judge those projects: many of these files are examples, and an approval can be set up in a place the scanner cannot
see. For that reason this page gives totals only.

Four more projects were planned (pydantic-ai, AutoGPT, anthropic-cookbook, awesome-llm-apps), but their downloads
did not finish in time, so they were not scanned.

| | |
|---|---|
| Python files read | 5,340 |
| Tools found | 1,570 |
| Listed, with code seen acting | 47 |
| Listed, on the name alone | 99 |

One project (Agno, with its large tool library) gives 135 of the 146 listings, so these numbers mostly describe how
the scanner reads that one project's code.

## How often a listing is right

This review was done by the AI that wrote the scanner, so it is not independent. 24 of the 47 code listings
were drawn at random (seed 20261031) and judged by reading what the tool does:

| Verdict | Count | Examples of what was seen |
|---|---:|---|
| Clearly an action a repeat could harm | 15 | booking, meeting, email, delete, write, run a command |
| Arguable: it only saves a local download | 5 | saving a chart or a file to a local folder |
| False alarm | 4 | a POST that only reads, or that only makes speech |

So 15 of 24 (63%) were clearly right and 4 of 24 (17%) were clearly wrong. With only 24 listings, the real rates
could be about 20 points higher or lower.

Name-only listings are weaker. Before the last change, 38 of 48 were small demo functions with almost no body, which
is correct about the name but shows no real action. 6 of the other 10 only edited a list in memory, so they were false
alarms. The rest were not judged. Treat a name-only listing as a question, not a finding.

## What it misses

In the one project where this was checked, 21 tool files had a send or POST call, and 14 of them were listed. Of the
7 not listed, most were searches, which is the intended result, and 2 (issue trackers and notes) look like real
misses, because the call goes through a helper the scanner does not follow. That is one project and one rough check,
so no overall miss rate is claimed.

It cannot see:
- an approval or guard set in a framework option, a middleware, another file or another service;
- tools built at runtime;
- what a call does past one helper in the same file;
- any language other than Python.

A clean scan does not mean an agent is safe.
