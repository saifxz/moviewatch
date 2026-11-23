from .base import SourceResult
from .district import DistrictSource
from .bookmyshow import BookMyShowSource

SOURCES = {
    "district": DistrictSource,
    "bookmyshow": BookMyShowSource,
}

__all__ = ["SourceResult", "DistrictSource", "BookMyShowSource", "SOURCES"]
