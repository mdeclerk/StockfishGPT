"""Serialized access to one long-lived Stockfish process."""

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Protocol, Self, TypeVar

import chess
import chess.engine

from .errors import (
    EngineBrokenError,
    EngineError,
    EngineNotFoundError,
    EngineNotStartedError,
    EngineProcessError,
    EngineRestartingError,
    EngineTimeoutError,
)

T = TypeVar("T")

_ENGINE_THREADS = 1
_ENGINE_HASH_MB = 64
_ENGINE_TIMEOUT_SECONDS = 5.0
_RESTART_COOLDOWN_SECONDS = 5.0


class _EngineTransport(Protocol):
    def close(self) -> None: ...

    def get_returncode(self) -> int | None: ...


class _UciProtocol(Protocol):
    async def configure(self, options: chess.engine.ConfigMapping) -> None: ...

    async def analyse(
        self,
        board: chess.Board,
        limit: chess.engine.Limit,
        *,
        multipv: int | None = None,
        info: chess.engine.Info = chess.engine.INFO_ALL,
        game: object | None = None,
    ) -> chess.engine.InfoDict | list[chess.engine.InfoDict]: ...

    async def quit(self) -> None: ...


EngineFactory = Callable[[str], Awaitable[tuple[_EngineTransport, _UciProtocol]]]


async def _open_uci(path: str) -> tuple[_EngineTransport, _UciProtocol]:
    transport, protocol = await chess.engine.popen_uci(path)
    return transport, protocol


class StockfishEngine:
    """Manage one Stockfish process and return raw analysis."""

    def __init__(
        self,
        executable: Path | None = None,
        *,
        factory: EngineFactory = _open_uci,
    ) -> None:
        self._executable = executable
        self._factory = factory
        self._transport: _EngineTransport | None = None
        self._protocol: _UciProtocol | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._restart_blocked_until = 0.0

    @property
    def is_alive(self) -> bool:
        return (
            self._started
            and self._protocol is not None
            and self._transport is not None
            and self._transport.get_returncode() is None
        )

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        async with self._lock:
            if self.is_alive:
                return
            self._discard_locked()
            await self._start_locked()

    async def close(self) -> None:
        async with self._lock:
            protocol = self._protocol
            transport = self._transport
            self._started = False
            self._restart_blocked_until = 0.0
            self._protocol = None
            self._transport = None
            if protocol is None:
                return

            try:
                # Teardown swallows engine errors: the transport close below
                # is the real cleanup, and a hung quit must not break shutdown.
                await self._await_engine("shutdown", protocol.quit())
            except EngineError:
                pass
            finally:
                if transport is not None:
                    transport.close()

    async def analyze(
        self,
        board: chess.Board,
        *,
        multipv: int,
        nodes: int,
    ) -> list[chess.engine.InfoDict]:
        if multipv < 1:
            raise ValueError("multipv must be positive")
        if nodes < 1:
            raise ValueError("nodes must be positive")

        async with self._lock:
            protocol = await self._ensure_live_locked()
            try:
                result = await self._await_engine(
                    "analysis",
                    protocol.analyse(
                        board,
                        chess.engine.Limit(nodes=nodes),
                        multipv=multipv,
                        info=chess.engine.INFO_ALL,
                        # Force `ucinewgame` so reused process state cannot
                        # perturb deterministic candidate selection.
                        game=object(),
                    ),
                )
            except EngineBrokenError:
                self._discard_locked()
                raise

        return result if isinstance(result, list) else [result]

    def _resolve_path(self) -> str:
        if self._executable is not None:
            executable = self._executable
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise EngineNotFoundError(executable)
            return str(executable)

        resolved = shutil.which("stockfish")
        if resolved is None:
            raise EngineNotFoundError
        return resolved

    async def _start_locked(self) -> None:
        path = self._resolve_path()
        transport, protocol = await self._await_engine("startup", self._factory(path))
        try:
            await self._await_engine(
                "configuration",
                protocol.configure(
                    {
                        "Threads": _ENGINE_THREADS,
                        "Hash": _ENGINE_HASH_MB,
                        "UCI_ShowWDL": True,
                    }
                ),
            )
        except EngineBrokenError:
            transport.close()
            raise

        self._transport = transport
        self._protocol = protocol
        self._started = True
        self._restart_blocked_until = 0.0

    async def _ensure_live_locked(self) -> _UciProtocol:
        if not self._started:
            raise EngineNotStartedError
        if self.is_alive:
            return self._require_protocol()

        remaining = self._restart_blocked_until - monotonic()
        if remaining > 0:
            raise EngineRestartingError(remaining)

        self._discard_locked()
        try:
            await self._start_locked()
        except EngineError:
            self._restart_blocked_until = monotonic() + _RESTART_COOLDOWN_SECONDS
            raise
        return self._require_protocol()

    def _discard_locked(self) -> None:
        # `_started` intentionally survives a discard: it means "owned" (the
        # caller may still restart this engine), not "a process exists".
        # Use `is_alive` to check for a live process; only `close()` releases
        # ownership by clearing `_started`.
        transport = self._transport
        self._protocol = None
        self._transport = None
        if transport is not None:
            transport.close()

    def _require_protocol(self) -> _UciProtocol:
        if self._protocol is None:
            raise EngineNotStartedError
        return self._protocol

    async def _await_engine(self, operation: str, awaitable: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(_ENGINE_TIMEOUT_SECONDS):
                return await awaitable
        except TimeoutError as error:
            raise EngineTimeoutError(operation, _ENGINE_TIMEOUT_SECONDS) from error
        except (chess.engine.EngineError, OSError) as error:
            raise EngineProcessError(operation, str(error)) from error
