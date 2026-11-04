"""``prefetch`` pipeline: read issuer files ahead of ``universe``, one worker per host."""

from .pipeline import create_pipeline

__all__ = ["create_pipeline"]
