"""``sectors`` pipeline: company ISIN -> GICS sector from FinanceDatabase, for rows whose source gives no sector."""

from .pipeline import create_pipeline

__all__ = ["create_pipeline"]
