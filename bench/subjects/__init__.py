# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""The subjects the benchmark runs: name -> "module:Class". Add yours here (see bench/README.md)."""

SUBJECTS = {
    "plain": "bench.subjects.plain_loop:PlainLoop",
    "langgraph": "bench.subjects.langgraph_graph:LangGraphGraph",
    "openai_agents": "bench.subjects.openai_agents_sdk:OpenAIAgents",
    "pydantic_ai": "bench.subjects.pydantic_ai_agent:PydanticAI",
    "google_adk": "bench.subjects.google_adk_agent:GoogleADK",
    "ms_agent_framework": "bench.subjects.ms_agent_framework:MSAgentFramework",
}

# Subjects that exist only to prove the benchmark can see a failure (bench/selfcheck.py). Never in RESULTS.md.
CONTROLS = {
    "control_double": "bench.selfcheck:DoublePay",
    "control_liar": "bench.selfcheck:Liar",
    "control_rewrite": "bench.selfcheck:Rewrite",
}

SUBJECTS_AND_CONTROLS = {**SUBJECTS, **CONTROLS}
