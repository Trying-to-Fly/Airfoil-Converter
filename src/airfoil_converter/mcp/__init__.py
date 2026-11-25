"""An MCP server, so another agent can drive the Converter without its window.

Needs the optional ``mcp`` extra (``pip install airfoil-converter[mcp]``); the
rest of the package never imports it. See :mod:`.server`.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The ``airfoil-converter-mcp`` command: says plainly when the extra is missing."""
    try:
        from . import server
    except ImportError as exc:
        if (exc.name or "").split(".")[0] != "mcp":
            raise
        print(
            "airfoil-converter-mcp needs the MCP SDK, which is an optional extra: "
            "pip install airfoil-converter[mcp]",
            file=sys.stderr,
        )
        return 2
    return server.main(argv)
