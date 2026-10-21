"""Build docs/demo.svg: an animated terminal of a REAL `call4agents quickstart` run.

    python docs/make_demo.py <saved quickstart output>

The lines come from the saved output file, unchanged; only colours and timing are added. Plain SVG
with CSS animation: it plays in GitHub READMEs, on the website and on phones, with no JavaScript.
If animation is off, every line is shown (the finished frame)."""
import html
import sys
from pathlib import Path

CYCLE = 16.0             # seconds per loop
STEP = 0.9               # seconds between lines


def colour(line: str) -> str:
    if "without" in line and ("emails sent: 2" in line or "2 emails" in line):
        return "#ff6b6b"
    if "REFUSED" in line or "with call4agents 1" in line or "emails sent: 1" in line:
        return "#4cd38a"
    if line.startswith("$") or line.startswith("OK"):
        return "#f2c14e"
    return "#d7dde4"


def build(lines):
    lines = ["$ pip install call4agents && call4agents quickstart"] + lines
    width, line_h, top = 940, 22, 58
    height = top + line_h * len(lines) + 24
    rules, texts = [], []
    for i, line in enumerate(lines):
        start = 100 * (0.6 + i * STEP) / CYCLE
        rules.append(f".l{i}{{animation:a{i} {CYCLE}s linear infinite}}"
                     f"@keyframes a{i}{{0%,{start:.1f}%{{opacity:0}}{start + 1.5:.1f}%,96%{{opacity:1}}100%{{opacity:0}}}}")
        texts.append(f'<text class="l{i}" x="22" y="{top + i * line_h}" fill="{colour(line)}">'
                     f"{html.escape(line) if line else ' '}</text>")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img"
 aria-label="call4agents quickstart: an agent crashes right after sending an email, then resumes. Without call4agents 2 emails are sent; with it, 1.">
<title>call4agents quickstart: without the kit 2 emails, with it 1</title>
<style>
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:14.5px;white-space:pre}}
{''.join(rules)}
@media (prefers-reduced-motion:reduce){{text{{animation:none!important;opacity:1!important}}}}
</style>
<rect width="100%" height="100%" rx="10" fill="#141a20"/>
<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/><circle cx="62" cy="20" r="6" fill="#28c840"/>
<text x="84" y="25" fill="#8b98a5" style="font-size:13px">An agent crashes right after sending an email, then resumes (real output, fake models, no API keys)</text>
{chr(10).join(texts)}
</svg>
"""


if __name__ == "__main__":
    raw = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    keep = [l.rstrip() for l in raw if l.strip() and not l.startswith("call4agents quickstart")]
    keep = [l.split("  Next:")[0] for l in keep]
    out = Path(__file__).with_name("demo.svg")
    out.write_text(build(keep), encoding="utf-8")
    print(f"wrote {out} ({len(keep)} lines)")
