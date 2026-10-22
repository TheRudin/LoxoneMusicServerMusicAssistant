"""Loxone wire format response serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    command: str
    name: str
    payload: Any
    raw: bool = False


def build_response(command: str, name: str, payload: Any, raw: bool = False) -> CommandResult:
    """Builds a named CommandResult."""
    return CommandResult(command=command, name=name, payload=payload, raw=raw)


def build_empty_response(command: str) -> CommandResult:
    """Builds an empty list response for unknown or silent commands."""
    return CommandResult(
        command=command,
        name=infer_result_name(command),
        payload=[],
    )


def build_not_implemented(command: str) -> CommandResult:
    """Convenience result for unhandled commands."""
    return CommandResult(
        command=command,
        name=infer_result_name(command),
        payload={"error": "not-implemented"},
    )


def infer_result_name(command: str) -> str:
    """Infers the result wrapper key from command URL segments."""
    parts = [p for p in (command or "").split("/") if p]
    for part in reversed(parts):
        if part and part[0].isalpha():
            return part
    return "response"


def serialize_result(result: CommandResult) -> str:
    """Serializes a CommandResult to the exact JSON wire format expected by Loxone."""
    if result.raw:
        if isinstance(result.payload, str):
            return result.payload
        return json.dumps(result.payload, indent=2)

    return json.dumps(
        {
            f"{result.name}_result": result.payload,
            "command": result.command,
        },
        indent=2,
    )
