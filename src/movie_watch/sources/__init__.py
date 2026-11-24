from .base import SourceResult
from .district import DistrictSource
from .bookmyshow import BookMyShowSource
from .bookmyshow_cdn import BookMyShowCdnSource

SOURCES = {
    "district": DistrictSource,
    "bookmyshow": BookMyShowSource,
    "bookmyshow_cdn": BookMyShowCdnSource,
}

__all__ = [
    "SourceResult",
    "DistrictSource",
    "BookMyShowSource",
    "BookMyShowCdnSource",
    "SOURCES",
]
