import re
from pathlib import Path
from typing import Optional

import yaml


class CategorizationService:
    def __init__(self):
        self.rules = self._load_rules()
        self.minimum_confidence_threshold = self.rules.get("minimum_confidence_threshold", 0.7)

    def _load_rules(self) -> dict:
        """Load categorization rules from YAML file."""
        rules_file = Path(__file__).parent / "rules.yaml"
        with open(rules_file, "r") as f:
            return yaml.safe_load(f)

    def categorize(
        self,
        filename: str,
        title: Optional[str] = None,
        channel_name: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> tuple[str, float, str]:
        """
        Categorize a file based on filename, title, and channel name.

        Returns:
            tuple: (category, confidence, source) where source is 'rule_based'
        """
        # Priority: filename > title > channel_name
        # Try filename first
        result = self._match_text(filename, file_type)
        if result:
            category, confidence = result
            if confidence >= self.minimum_confidence_threshold:
                return category, confidence, "rule_based"

        # Try title
        if title:
            result = self._match_text(title, file_type)
            if result:
                category, confidence = result
                if confidence >= self.minimum_confidence_threshold:
                    return category, confidence, "rule_based"

        # Try channel name
        if channel_name:
            result = self._match_text(channel_name, file_type)
            if result:
                category, confidence = result
                if confidence >= self.minimum_confidence_threshold:
                    return category, confidence, "rule_based"

        # If no match or below threshold, return uncategorized
        return "uncategorized", 0.0, "rule_based"

    def _match_text(self, text: str, file_type: Optional[str] = None) -> Optional[tuple[str, float]]:
        """
        Match text against all rules and return the best match.

        Returns:
            tuple: (category_name, confidence) or None
        """
        best_match = None
        best_confidence = 0.0

        for category_rule in self.rules.get("categories", []):
            category_name = category_rule.get("name")
            confidence = category_rule.get("confidence", 0.5)
            patterns = category_rule.get("patterns", [])
            keywords = category_rule.get("keywords", [])

            # Check patterns
            for pattern in patterns:
                try:
                    if re.search(pattern, text):
                        if confidence > best_confidence:
                            best_match = category_name
                            best_confidence = confidence
                        break
                except re.error:
                    # Skip invalid regex patterns
                    continue

            # Check keywords
            for keyword in keywords:
                if re.search(rf"(?i)\b{re.escape(keyword)}\b", text):
                    if confidence > best_confidence:
                        best_match = category_name
                        best_confidence = confidence
                    break

        return (best_match, best_confidence) if best_match else None


# Singleton instance
_categorization_service: Optional[CategorizationService] = None


def get_categorization_service() -> CategorizationService:
    """Get the singleton categorization service instance."""
    global _categorization_service
    if _categorization_service is None:
        _categorization_service = CategorizationService()
    return _categorization_service
