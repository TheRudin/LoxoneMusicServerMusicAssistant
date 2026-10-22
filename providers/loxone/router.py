"""Loxone command router with prefix and regex matching."""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Match, Optional, Tuple, Union
from urllib.parse import unquote

from .responses import CommandResult, build_empty_response

logger = logging.getLogger("loxone.router")

HandlerFunc = Callable[[str, Optional[bytes], Optional[Match[str]]], Awaitable[CommandResult]]


class LoxoneRouter:
    """Dispatches Loxone commands based on path prefixes and regex patterns."""

    def __init__(self) -> None:
        self.prefix_routes: Dict[str, HandlerFunc] = {}
        self.regex_routes: List[Tuple[re.Pattern[str], HandlerFunc]] = []

    def register_prefix(self, prefix: str, handler: HandlerFunc) -> None:
        """Registers a handler for a command prefix (e.g. 'audio/cfg/ready')."""
        clean_prefix = prefix.strip("/")
        self.prefix_routes[clean_prefix] = handler

    def register_regex(self, pattern: Union[str, re.Pattern[str]], handler: HandlerFunc) -> None:
        """Registers a handler for a regular expression pattern."""
        if isinstance(pattern, str):
            compiled = re.compile(pattern)
        else:
            compiled = pattern
        self.regex_routes.append((compiled, handler))

    async def dispatch(self, command: str, payload: Optional[bytes] = None) -> CommandResult:
        """Matches and dispatches a command to its registered handler."""
        clean_command = command.strip("/")
        # URL decode path for matching if needed, while keeping raw command for response
        decoded_command = unquote(clean_command)

        # 1. Exact or prefix match
        for prefix, handler in self.prefix_routes.items():
            if clean_command == prefix or clean_command.startswith(f"{prefix}/") or decoded_command.startswith(f"{prefix}/"):
                try:
                    return await handler(command, payload, None)
                except Exception as exc:
                    logger.exception("Error executing prefix handler for %s: %s", command, exc)
                    return build_empty_response(command)

        # 2. Regex match
        for pattern, handler in self.regex_routes:
            match = pattern.search(clean_command) or pattern.search(decoded_command)
            if match:
                try:
                    return await handler(command, payload, match)
                except Exception as exc:
                    logger.exception("Error executing regex handler for %s: %s", command, exc)
                    return build_empty_response(command)

        logger.debug("Unhandled Loxone command: %s", command)
        return build_empty_response(command)
