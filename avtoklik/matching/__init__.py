"""Сопоставление объявлений: нормализация идентификаторов и дедупликация.

Публичный API модуля:

* :mod:`avtoklik.matching.plate` — госномер (уровень 1 каскада);
* :mod:`avtoklik.matching.vin` — VIN (уровень 0 каскада);
* :mod:`avtoklik.matching.dedup` — сам каскад, кластеризация и каноническая цена.
"""

from avtoklik.matching.dedup import (
    CanonicalPrice,
    ListingFeatures,
    MatchConfidence,
    MatchResult,
    attr_similarity,
    canonical_price,
    cluster_listings,
    describe_reasons,
    hamming_distance,
    similarity_score,
)
from avtoklik.matching.plate import (
    PLATE_LETTERS,
    ParsedPlate,
    PlateKind,
    PlateParseError,
    explain_plate_error,
    is_valid_plate,
    normalize_plate,
    parse_plate,
)
from avtoklik.matching.vin import (
    VIN_LENGTH,
    has_valid_check_digit,
    is_valid_vin,
    normalize_vin,
    vin_check_digit,
)

__all__ = [
    "PLATE_LETTERS",
    "VIN_LENGTH",
    "CanonicalPrice",
    "ListingFeatures",
    "MatchConfidence",
    "MatchResult",
    "ParsedPlate",
    "PlateKind",
    "PlateParseError",
    "attr_similarity",
    "canonical_price",
    "cluster_listings",
    "describe_reasons",
    "explain_plate_error",
    "hamming_distance",
    "has_valid_check_digit",
    "is_valid_plate",
    "is_valid_vin",
    "normalize_plate",
    "normalize_vin",
    "parse_plate",
    "similarity_score",
    "vin_check_digit",
]
