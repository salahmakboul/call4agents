"""Types for kit check K13 (must be importable by module path for replay)."""
import dataclasses


@dataclasses.dataclass
class Order:
    id: int
    items: tuple


class FakeModel:
    """Pydantic-style: model_dump / model_validate (no pydantic needed)."""

    def __init__(self, text, tokens):
        self.text, self.tokens = text, tokens

    def model_dump(self, mode="python"):
        return {"text": self.text, "tokens": self.tokens}

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def __eq__(self, other):
        return isinstance(other, FakeModel) and (self.text, self.tokens) == (other.text, other.tokens)


try:                                   # K16: a REAL pydantic model
    import datetime as _dt

    from pydantic import BaseModel

    class Usage(BaseModel):
        prompt_tokens: int
        completion_tokens: int

    class ModelReply(BaseModel):
        text: str
        created: _dt.datetime
        usage: Usage
except ImportError:                    # pragma: no cover
    ModelReply = Usage = None   # both must exist, or the kit suite crashes without pydantic


# K19 canaries: a crafted record naming these must be REFUSED, never called.
CALLED = []


def canary(**kw):
    CALLED.append(kw)
    return "ran"


canary.model_validate = lambda data: CALLED.append(data)


class NotAModel:
    """Has model_validate but no model_dump: not a model, must not be called."""

    @classmethod
    def model_validate(cls, data):
        CALLED.append(data)
        return cls()
