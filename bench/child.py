# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""One step of one scenario, in its own process. Started by bench/run.py; not meant to be run by hand.

    python -m bench.child <subject> <kit 0|1> <step> <work> <run_id> <repeat> <json argument>

Prints one line "BENCH_RESULT <json>" when the step returns, or "BENCH_ERROR <type>: <message>" and exits
3 when it raises. A process the benchmark killed on purpose exits 137 and prints nothing.
"""
import importlib
import json
import os
import sys
import traceback

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
os.environ.setdefault("OPENAI_API_KEY", "not-used-scripted-model")


def load_subject(spec: str):
    module, _, cls = spec.partition(":")
    return getattr(importlib.import_module(module), cls)()


def main(argv) -> int:
    from bench.subject import Env
    from bench.subjects import SUBJECTS_AND_CONTROLS
    name, kit, step, work, run_id, repeat, arg = argv
    subject = load_subject(SUBJECTS_AND_CONTROLS[name])
    # Import the framework (and the kit) BEFORE the S3 barrier in Env(): two workers must then start the run
    # itself together, not their imports, which take a few seconds and would let one finish before the other.
    for module in subject.requires + (("call4agents",) if kit == "1" else ()):
        importlib.import_module(module)
    env = Env(work, run_id, kit == "1", repeat=int(repeat))
    arg = json.loads(arg)
    try:
        if step == "start":
            out = subject.start(env)
        elif step == "resume":
            out = subject.resume(env)
        elif step == "start_approval":
            out = subject.start_approval(env)
        elif step == "decide":
            out = subject.decide(env, arg["shown"], arg["decision"])
        elif step == "resume_approved":
            out = subject.resume_approved(env, arg["message"])
        elif step == "tamper":
            out = subject.tamper(env, arg["new_args"])
        else:
            raise ValueError(f"unknown step {step!r}")
    except BaseException as exc:  # noqa: BLE001 - the benchmark records how a step ended, whatever it was
        traceback.print_exc()
        print(f"BENCH_ERROR {type(exc).__name__}: {str(exc)[:300]}".replace("\n", " "), flush=True)
        report_kit()
        return 3
    report_kit()
    print("BENCH_RESULT " + json.dumps(out, default=str), flush=True)
    return 0


def report_kit() -> None:
    """Which call4agents this process really used, so the benchmark can check the variant is the one named."""
    kit = sys.modules.get("call4agents")
    if kit is not None:
        from pathlib import Path
        print(f"BENCH_KIT {kit.__version__} {Path(kit.__file__).resolve().parents[1]}", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
