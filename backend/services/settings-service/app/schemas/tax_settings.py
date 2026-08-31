from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

FILER_STATUS = Literal["filer", "non_filer"]


class TaxSettingsUpdate(BaseModel):
    default_gst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    withholding_tax_rate: Optional[float] = Field(default=None, ge=0, le=100)
    filer_status: Optional[FILER_STATUS] = None


class TaxSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_gst_rate: float
    withholding_tax_rate: float
    filer_status: str
    created_at: datetime
    updated_at: datetime
