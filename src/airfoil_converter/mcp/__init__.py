"""An MCP server, so another agent can drive the Converter without its window.

Needs the optional ``mcp`` extra. From the checkout, install it with
``python -m pip install -e ".[mcp]"``. See :mod:`.server`.
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
            "airfoil-converter-mcp needs the optional MCP SDK. On Python 3.10 or newer, "
            'run python -m pip install -e ".[mcp]" from the repository checkout.',
            file=sys.stderr,
        )
        return 2
    return server.main(argv)
