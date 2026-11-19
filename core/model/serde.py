"""dataclass <-> JSON 的通用序列化，避免给每个类手写 to_dict。"""

from __future__ import annotations

import types
from dataclasses import MISSING, fields, is_dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def from_jsonable(cls: type, data: dict) -> Any:
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            if f.default is not MISSING:
                continue
            if f.default_factory is not MISSING:  # type: ignore[misc]
                continue
            raise ValueError(f"缺少字段 {cls.__name__}.{f.name}")
        kwargs[f.name] = _decode(hints[f.name], data[f.name])
    return cls(**kwargs)


def _decode(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        return _decode(args[0], value) if args else value
    if origin is dict:
        args = get_args(tp)
        if not args:
            return dict(value)
        return {k: _decode(args[1], v) for k, v in value.items()}
    if origin in (list, tuple):
        args = get_args(tp)
        if not args:
            return list(value)
        return [_decode(args[0], v) for v in value]
    if is_dataclass(tp) and isinstance(value, dict):
        return from_jsonable(tp, value)
    return value
