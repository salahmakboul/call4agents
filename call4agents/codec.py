# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Salah Eddine Makboul
"""Typed JSON for recorded results: replay gives back the same TYPE, not text.

Supported without help: JSON values, tuple, set, frozenset, bytes, datetime/date/time,
Decimal, dataclasses, and objects with model_dump()/model_validate() (Pydantic-style,
which covers most LLM SDK responses). Anything else raises NotRecordable at RECORD time -
never a silent change of type at replay time. Pass encode=/decode= to @recorded for others.
No pickle: loading a pickle can run code.
"""

import base64
import dataclasses
import datetime as _dt
import decimal
import importlib
from typing import Any

TAG = "__c4a__"


class NotRecordable(TypeError):
    pass


def _cls_path(obj: Any) -> str:
    t = type(obj)
    return f"{t.__module__}:{t.__qualname__}"


def _load_cls(path: str, kind: str):
    """Only a real dataclass type ("dataclass") or a real class with model_validate AND model_dump
    ("model") is ever called. A record is data: a crafted one naming `os:system` must not run it
    (security review, 2026-10-16)."""
    if not isinstance(path, str) or ":" not in path:
        raise ValueError(f"bad recorded class path {path!r}")
    mod, _, qual = path.partition(":")
    obj = importlib.import_module(mod)
    for part in qual.split("."):
        if part.startswith("__"):
            raise ValueError(f"refusing recorded class path {path!r}")
        obj = getattr(obj, part)
    if not isinstance(obj, type):
        raise ValueError(f"recorded {kind} {path!r} is not a class; refusing to call it")
    if kind == "dataclass" and not dataclasses.is_dataclass(obj):
        raise ValueError(f"recorded dataclass {path!r} is not a dataclass; refusing to call it")
    if kind == "model" and not (hasattr(obj, "model_validate") and hasattr(obj, "model_dump")):
        raise ValueError(f"recorded model {path!r} has no model_validate/model_dump; refusing to call it")
    return obj


def encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and TAG not in value:
            return {k: encode(v) for k, v in value.items()}
        return {TAG: "dict", "v": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple) and not hasattr(value, "_fields"):
        return {TAG: "tuple", "v": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {TAG: type(value).__name__, "v": [encode(v) for v in value]}
    if isinstance(value, (bytes, bytearray)):
        return {TAG: "bytes", "v": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, _dt.datetime):
        return {TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, _dt.date):
        return {TAG: "date", "v": value.isoformat()}
    if isinstance(value, _dt.time):
        return {TAG: "time", "v": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {TAG: "decimal", "v": str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {TAG: "dataclass", "cls": _cls_path(value),
                "v": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if hasattr(value, "model_dump") and hasattr(type(value), "model_validate"):
        return {TAG: "model", "cls": _cls_path(value), "v": encode(value.model_dump(mode="json"))}
    raise NotRecordable(f"cannot record a {type(value).__name__} exactly; pass encode=/decode= to @recorded")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    tag = value.get(TAG)
    if tag is None:
        return {k: decode(v) for k, v in value.items()}
    v = value.get("v")
    if tag == "dict":
        return {decode(k): decode(x) for k, x in v}
    if tag == "tuple":
        return tuple(decode(x) for x in v)
    if tag == "set":
        return {decode(x) for x in v}
    if tag == "frozenset":
        return frozenset(decode(x) for x in v)
    if tag == "bytes":
        return base64.b64decode(v)
    if tag == "datetime":
        return _dt.datetime.fromisoformat(v)
    if tag == "date":
        return _dt.date.fromisoformat(v)
    if tag == "time":
        return _dt.time.fromisoformat(v)
    if tag == "decimal":
        return decimal.Decimal(v)
    if tag == "dataclass":
        return _load_cls(value["cls"], "dataclass")(**{k: decode(x) for k, x in v.items()})
    if tag == "model":
        return _load_cls(value["cls"], "model").model_validate(decode(v))
    raise ValueError(f"unknown recorded type {tag!r}")
