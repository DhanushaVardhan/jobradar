"""Source connector base class."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar

from jobradar.http import Http, HttpError
from jobradar.models import TargetResult

if TYPE_CHECKING:
    from jobradar.config import Config

log = logging.getLogger(__name__)


class Source:
    """A connector turns one external API into normalised `Job` objects."""

    name: ClassVar[str] = "base"
    via: ClassVar[str | None] = None  # attribution for aggregators
    requires_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, http: Http, config: Config, known_ids: set[str] | None = None):
        self.http = http
        self.config = config
        self.opts: dict[str, Any] = config.source_settings(self.name)
        self.known_ids = known_ids or set()

    # --------------------------------------------------------------------------
    def enabled(self) -> bool:
        if not self.opts.get("enabled", True):
            return False
        return all(self.config.env(k) for k in self.requires_env)

    def skip_reason(self) -> str | None:
        if not self.opts.get("enabled", True):
            return "disabled in settings.yaml"
        missing = [k for k in self.requires_env if not self.config.env(k)]
        return f"missing env: {', '.join(missing)}" if missing else None

    async def fetch(self) -> list[TargetResult]:
        raise NotImplementedError

    # --------------------------------------------------------------------------
    async def guarded(self, target: str, fn: Callable[[], Awaitable[TargetResult]]) -> TargetResult:
        """Run one target fetch; never raise, always return a TargetResult."""
        try:
            return await fn()
        except HttpError as exc:
            log.warning("%s failed: %s", target, exc)
            return TargetResult(target=target, source=self.name, ok=False, error=str(exc)[:300])
        except Exception as exc:  # parsing bugs must not kill the run
            log.exception("%s crashed", target)
            return TargetResult(
                target=target,
                source=self.name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    async def gather(self, coros: list[Awaitable[TargetResult]]) -> list[TargetResult]:
        return list(await asyncio.gather(*coros))
