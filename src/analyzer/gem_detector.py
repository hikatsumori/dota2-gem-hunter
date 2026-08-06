"""
Gem detection module for Dota 2 socketed items.

Uses rapidfuzz to match gem names from listing descriptions against a curated
database of Ethereal and Prismatic gems with canonical names and aliases.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from src.steam.models import Gem, GemType

logger = logging.getLogger(__name__)


class GemDetector:
    """
    Detects Ethereal and Prismatic gems in Steam Market listing descriptions.

    Loads a gem database on initialization and provides fuzzy matching
    with confidence scoring against canonical gem names and aliases.
    """

    # Confidence thresholds
    EXACT_THRESHOLD = 100  # rapidfuzz score for exact match
    FUZZY_THRESHOLD = 85   # rapidfuzz score for fuzzy match
    PARTIAL_THRESHOLD = 70  # rapidfuzz score for partial match

    # Confidence values corresponding to thresholds
    EXACT_CONFIDENCE = 1.0
    FUZZY_CONFIDENCE = 0.8
    PARTIAL_CONFIDENCE = 0.5

    def __init__(self, gem_db_path: str | Path | None = None) -> None:
        """
        Initialize the GemDetector.

        Args:
            gem_db_path: Optional path to gems.json. Defaults to data/gems.json
                         relative to this file.
        """
        if gem_db_path is None:
            gem_db_path = Path(__file__).parent.parent.parent / "data" / "gems.json"

        self._gem_db_path = Path(gem_db_path)
        self._gem_entries: list[dict[str, Any]] = []  # Flat list for rapidfuzz
        self._canonical_to_type: dict[str, GemType] = {}  # canonical name -> GemType
        self._load_gem_database()

    def _load_gem_database(self) -> None:
        """Load gem database from JSON file and build lookup structures."""
        try:
            with open(self._gem_db_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._gem_entries = []
            self._canonical_to_type = {}

            for gem_type_str, gems in data.items():
                if gem_type_str == "ethereal":
                    gem_type = GemType.ETHEREAL
                elif gem_type_str == "prismatic":
                    gem_type = GemType.PRISMATIC
                else:
                    gem_type = GemType.REGULAR

                for gem in gems:
                    canonical = gem["name"]
                    aliases = gem.get("aliases", [])

                    # Build flat list for rapidfuzz: (searchable_name, canonical_name, gem_type)
                    # Include canonical name
                    self._gem_entries.append(
                        {
                            "searchable": canonical.lower(),
                            "canonical": canonical,
                            "gem_type": gem_type,
                        }
                    )
                    self._canonical_to_type[canonical.lower()] = gem_type

                    # Include aliases
                    for alias in aliases:
                        self._gem_entries.append(
                            {
                                "searchable": alias.lower(),
                                "canonical": canonical,
                                "gem_type": gem_type,
                            }
                        )

            logger.info(
                "Loaded gem database: %d ethereal + %d prismatic = %d total searchable entries",
                len([e for e in self._gem_entries if e["gem_type"] == GemType.ETHEREAL]),
                len([e for e in self._gem_entries if e["gem_type"] == GemType.PRISMATIC]),
                len(self._gem_entries),
            )

        except Exception as e:
            logger.error("Failed to load gem database from %s: %s", self._gem_db_path, e)
            self._gem_entries = []
            self._canonical_to_type = {}

    def _extract_text_from_html(self, html: str) -> str:
        """Extract plain text from HTML for matching."""
        # Simple HTML tag removal - preserve structure with newlines
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"</(div|p|tr|td|li)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _extract_socket_index(self, text: str, gem_position: int) -> int | None:
        """
        Try to extract socket index from text near the gem mention.

        Looks for patterns like "Socket 0:", "Socket 1", etc. near the matched position.
        """
        # Search backwards from gem position for socket pattern
        search_window = text[max(0, gem_position - 200) : gem_position]

        # Look for "Socket N:" or "Socket N" pattern
        socket_matches = list(re.finditer(r"Socket\s*(\d+)\s*:?", search_window, re.IGNORECASE))
        if socket_matches:
            # Return the last (closest) socket index found
            return int(socket_matches[-1].group(1))

        # Also check forward a bit
        search_window_forward = text[gem_position : gem_position + 100]
        socket_matches = list(re.finditer(r"Socket\s*(\d+)\s*:?", search_window_forward, re.IGNORECASE))
        if socket_matches:
            return int(socket_matches[0].group(1))

        return None

    def _score_match(self, score: int) -> float:
        """
        Convert rapidfuzz score (0-100) to confidence value.

        exact (100)     -> 1.0
        fuzzy (>=85)    -> 0.8
        partial (>=70)  -> 0.5
        below 70        -> 0.0 (not a match)
        """
        if score >= self.EXACT_THRESHOLD:
            return self.EXACT_CONFIDENCE
        elif score >= self.FUZZY_THRESHOLD:
            return self.FUZZY_CONFIDENCE
        elif score >= self.PARTIAL_THRESHOLD:
            return self.PARTIAL_CONFIDENCE
        return 0.0

    def detect_gems(self, description_html: str) -> list[Gem]:
        """
        Detect gems in a listing description HTML.

        Uses rapidfuzz to match gem names (canonical + aliases) against
        the description text. Returns Gem objects with type, name, socket index,
        rarity flag, and confidence score.

        Strategy:
        1. Extract text from HTML and split into lines
        2. For each line, find the best matching gem using token_set_ratio
           (handles word order variations better than partial_ratio)
        3. Also do exact substring search for canonical names/aliases as a fast path
        4. Deduplicate by canonical name, keeping highest confidence

        Args:
            description_html: Raw HTML of the listing description

        Returns:
            List of Gem objects (deduplicated by canonical name, highest confidence kept)
        """
        if not description_html or not description_html.strip():
            return []

        if not self._gem_entries:
            logger.warning("Gem database not loaded, cannot detect gems")
            return []

        text = self._extract_text_from_html(description_html)
        text_lower = text.lower()

        # Build lookup maps for exact substring matching (fast path)
        searchable_to_entry: dict[str, dict] = {e["searchable"]: e for e in self._gem_entries}

        # Split text into lines for line-by-line matching
        # This prevents "Flame" in one line from matching "Dreaded Flame" in another context
        lines = [line.strip() for line in text_lower.split("\n") if line.strip()]

        # Process matches: build Gem candidates with confidence
        gem_candidates: dict[str, tuple[Gem, float]] = {}  # canonical_lower -> (Gem, confidence)

        # Strategy 1: Exact substring search (fast, high confidence)
        for searchable, entry in searchable_to_entry.items():
            if searchable in text_lower:
                # Find position for socket index extraction
                pos = text_lower.find(searchable)
                canonical = entry["canonical"]
                canonical_lower = canonical.lower()
                gem_type = entry["gem_type"]

                confidence = self.EXACT_CONFIDENCE  # Exact substring match = 1.0
                socket_index = self._extract_socket_index(text, pos) if pos != -1 else None
                is_rare = gem_type in (GemType.ETHEREAL, GemType.PRISMATIC)

                gem = Gem(
                    id=0,
                    listing_id=0,
                    gem_type=gem_type,
                    gem_name=canonical,
                    socket_index=socket_index,
                    is_rare=is_rare,
                    created_at=datetime.utcnow(),
                )

                if canonical_lower not in gem_candidates or confidence > gem_candidates[canonical_lower][1]:
                    gem_candidates[canonical_lower] = (gem, confidence)

        # Strategy 2: Line-by-line fuzzy matching for non-exact matches
        # Use token_set_ratio which is better for multi-word names
        searchable_names = [entry["searchable"] for entry in self._gem_entries]

        for line in lines:
            if len(line) < 3:  # Skip very short lines
                continue

            # Find best match for this line
            matches = process.extract(
                line,
                searchable_names,
                scorer=fuzz.token_set_ratio,
                score_cutoff=self.PARTIAL_THRESHOLD,
                limit=3,  # Top 3 matches per line
            )

            for match in matches:
                matched_name, score, idx = match
                entry = self._gem_entries[idx]
                canonical = entry["canonical"]
                gem_type = entry["gem_type"]
                canonical_lower = canonical.lower()

                confidence = self._score_match(int(score))
                if confidence == 0.0:
                    continue

                # Skip if we already have an exact match for this canonical
                if canonical_lower in gem_candidates and gem_candidates[canonical_lower][1] >= self.EXACT_CONFIDENCE:
                    continue

                # Find position in original text for socket index extraction
                pos = text_lower.find(matched_name)
                if pos == -1:
                    pos = text_lower.find(canonical_lower)
                socket_index = self._extract_socket_index(text, pos) if pos != -1 else None

                is_rare = gem_type in (GemType.ETHEREAL, GemType.PRISMATIC)

                gem = Gem(
                    id=0,
                    listing_id=0,
                    gem_type=gem_type,
                    gem_name=canonical,
                    socket_index=socket_index,
                    is_rare=is_rare,
                    created_at=datetime.utcnow(),
                )

                # Deduplicate: keep highest confidence for each canonical gem
                if canonical_lower not in gem_candidates or confidence > gem_candidates[canonical_lower][1]:
                    gem_candidates[canonical_lower] = (gem, confidence)

        # Sort by confidence (descending) then by socket_index (ascending, None last)
        sorted_gems = sorted(
            gem_candidates.values(),
            key=lambda x: (-x[1], x[0].socket_index if x[0].socket_index is not None else 999),
        )

        result = [gem for gem, _ in sorted_gems]

        if result:
            logger.debug(
                "Detected %d gems: %s",
                len(result),
                [f"{g.gem_name}({g.gem_type.value}, conf={next(c for g2, c in gem_candidates.values() if g2.gem_name == g.gem_name):.1f}, sock={g.socket_index})" for g in result],
            )

        return result


# Convenience function for direct usage
def create_gem_detector(gem_db_path: str | Path | None = None) -> GemDetector:
    """
    Create a GemDetector instance.

    Args:
        gem_db_path: Optional path to gems.json

    Returns:
        Configured GemDetector instance
    """
    return GemDetector(gem_db_path)