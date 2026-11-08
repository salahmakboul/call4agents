"""After start + approve-with-crash: which checkpoints exist and which hold the approved response (decoded state)."""
import base64, json, os, subprocess, sys, tempfile
from pathlib import Path
here = Path(__file__).parent
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
    for ph, kill in (("start", "0"), ("approve", "1")):
        subprocess.run([sys.executable, str(here / "maf_approve_crash.py"), "--phase", d, ph],
                       env=dict(os.environ, KILL_AFTER_PAY=kill), capture_output=True)
    rows = []
    for f in (Path(d) / "ckpt").glob("*.json"):
        j = json.loads(f.read_text(encoding="utf-8"))
        blob = json.dumps(j)
        dec = b""
        def walk(x):
            global dec
            if isinstance(x, dict): [walk(v) for v in x.values()]
            elif isinstance(x, list): [walk(v) for v in x]
            elif isinstance(x, str) and len(x) > 40:
                try: dec += base64.b64decode(x, validate=True)
                except Exception: pass
        walk(j)
        text = blob.encode() + dec
        rows.append((j.get("timestamp"), j.get("iteration_count"), len(j.get("pending_request_info_events") or {}),
                     b"function_approval_response" in text, b"function_result" in text))
    for r in sorted(rows):
        print("ts", r[0][11:23], "iter", r[1], "pending", r[2], "holds approval response", r[3], "holds tool result", r[4])
