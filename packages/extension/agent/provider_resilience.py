"""Provider failure handling utilities for production hardening."""

from __future__ import annotations

from dataclasses import dataclass


class ProviderCallError(RuntimeError):
    """Raised when an upstream provider call fails."""


@dataclass(frozen=True)
class ProviderCallResult:
    provider: str
    model: str
    output_text: str


class ProviderResilienceService:
    """Executes provider calls with deterministic failure surface."""

    async def execute_test_call(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        force_failure: bool,
    ) -> ProviderCallResult:
        if force_failure:
            raise ProviderCallError(f"Provider '{provider}' call failed")
        return ProviderCallResult(
            provider=provider,
            model=model,
            output_text=f"simulated-response:{prompt[:64]}",
        )
