"""Build docs/crashtest.svg: an animated terminal of a REAL `call4agents crashtest --demo` run.

    call4agents crashtest --demo > saved.txt
    python docs/make_crashtest_demo.py saved.txt

The lines come from the saved output, unchanged; only colours and timing are added. Same format as make_demo.py."""
import html
import sys
from pathlib import Path

CYCLE = 28.0
STEP = 1.0


def colour(line: str) -> str:
    if line.startswith("$"):
        return "#f2c14e"
    if "RAN TWICE" in line or line.startswith("A small agent"):
        return "#ff6b6b"
    if line.startswith("No action ran twice") or line.startswith("The same agent"):
        return "#4cd38a"
    return "#d7dde4"


def build(lines):
    lines = ["$ pip install call4agents && call4agents crashtest --demo"] + lines
    width, line_h, top = 940, 22, 58
    height = top + line_h * len(lines) + 24
    rules, texts = [], []
    for i, line in enumerate(lines):
        start = 100 * (0.6 + i * STEP) / CYCLE
        rules.append(f".l{i}{{animation:a{i} {CYCLE}s linear infinite}}"
                     f"@keyframes a{i}{{0%,{start:.1f}%{{opacity:0}}{start + 1.5:.1f}%,96%{{opacity:1}}100%{{opacity:0}}}}")
        texts.append(f'<text class="l{i}" x="22" y="{top + i * line_h}" fill="{colour(line)}">{html.escape(line) if line else " "}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img"
 aria-label="call4agents crashtest: a small agent is killed at every step and restarted. Without the guard its refund runs twice; with one line added it does not.">
<title>call4agents crashtest: the refund ran twice, then one line fixes it</title>
<style>
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:14.5px;white-space:pre}}
{''.join(rules)}
@media (prefers-reduced-motion:reduce){{text{{animation:none!important;opacity:1!important}}}}
</style>
<rect width="{width}" height="{height}" rx="10" fill="#14181f"/>
<circle cx="22" cy="20" r="6" fill="#ff5f56"/><circle cx="42" cy="20" r="6" fill="#ffbd2e"/><circle cx="62" cy="20" r="6" fill="#27c93f"/>
{''.join(texts)}
</svg>
"""


if __name__ == "__main__":
    saved = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    Path(__file__).with_name("crashtest.svg").write_text(build(saved), encoding="utf-8")
