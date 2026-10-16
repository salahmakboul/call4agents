"""Optional suite: a REAL OpenAI SDK ChatCompletion object survives
record -> replay with the same class and fields. Built locally from a dict: no network, no key.
Skips (exit 0) when the openai package is not installed.
Run: python -m tests.sdk_types_replay"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from openai.types.chat import ChatCompletion
except ImportError:
    print("SKIPPED: openai SDK not installed (optional suite)")
    sys.exit(0)

import call4agents as c4a  # noqa: E402

RAW = {"id": "chatcmpl-1", "object": "chat.completion", "created": 1760600000, "model": "gpt-test",
       "choices": [{"index": 0, "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": None,
                                "tool_calls": [{"id": "call_1", "type": "function",
                                                "function": {"name": "pay", "arguments": "{\"amount\": 10}"}}]}}],
       "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}}


def main() -> int:
    original = ChatCompletion.model_validate(RAW)
    d = Path(tempfile.mkdtemp(prefix="c4a-sdk-"))
    c4a.init(str(d / "rec.sqlite"), secret=b"s")

    @c4a.recorded
    def chat(prompt):
        return original

    with c4a.run("sdk"):
        chat("pay alice")
    c4a.init(str(d / "rep.sqlite"), secret=b"s", replay_from=str(d / "rec.sqlite"))
    with c4a.run("sdk"):
        back = chat("pay alice")
    ok = (type(back) is ChatCompletion and back == original
          and back.choices[0].message.tool_calls[0].function.arguments == "{\"amount\": 10}")
    print("sdk replay checks pass" if ok else f"SDK REPLAY CHECKS FAILED: {type(back).__name__}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
