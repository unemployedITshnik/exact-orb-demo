"""Aspect configuration detection."""

from .finder import find_configurations
from .types import (
    Configuration,
    ConfigurationCategory,
    ConfigurationCategoryThresholds,
    ConfigurationConfig,
    ConfigurationType,
)

__all__ = [
    "Configuration",
    "ConfigurationCategory",
    "ConfigurationCategoryThresholds",
    "ConfigurationConfig",
    "ConfigurationType",
    "find_configurations",
]
