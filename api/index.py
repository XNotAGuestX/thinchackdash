"""Vercel entry point: reuse the local backend handler for /api/* requests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from backend.ai import AiHelp  # noqa: E402
from backend.server import Handler  # noqa: E402

_AI = AiHelp()  # Per warm instance only; serverless does not keep it between cold starts.


class handler(Handler):  # noqa: N801 - Vercel looks up this exact name.
    def setup(self) -> None:
        self.server.ai = _AI  # type: ignore[attr-defined]
        super().setup()
