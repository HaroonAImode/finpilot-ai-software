"""Response shapes — exactly LiteParse's own documented OCR_API_SPEC.md
contract (https://github.com/run-llama/liteparse/blob/main/OCR_API_SPEC.md),
confirmed via that doc directly during the migration, not invented:
POST /ocr, `file` + optional `language` in; `{"results": [...]}` out, each
result `{text, bbox: [x1,y1,x2,y2], confidence, polygon}`.
"""
from typing import Optional

from pydantic import BaseModel


class OcrResultItem(BaseModel):
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float
    polygon: Optional[list[tuple[float, float]]] = None


class OcrResponse(BaseModel):
    results: list[OcrResultItem]
