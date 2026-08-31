from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AutomationSettingsUpdate(BaseModel):
    auto_categorize_expenses: Optional[bool] = None
    auto_detect_duplicates: Optional[bool] = None
    auto_insights: Optional[bool] = None
    smart_vendor_suggestions: Optional[bool] = None
    enabled_ai: Optional[bool] = None


class AutomationSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    auto_categorize_expenses: bool
    auto_detect_duplicates: bool
    auto_insights: bool
    smart_vendor_suggestions: bool
    enabled_ai: bool
    created_at: datetime
    updated_at: datetime
