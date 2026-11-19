"""Internal, declarative foundations for future agentic capabilities.

This package does not execute business operations or call an AI provider.
"""

from app.agentic.registry import CapabilityNotFoundError, CapabilityRegistry, build_default_registry

__all__ = ["CapabilityNotFoundError", "CapabilityRegistry", "build_default_registry"]
