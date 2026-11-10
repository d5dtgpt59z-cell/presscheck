from pydantic import BaseModel, Field, model_validator

class Config(BaseModel):
    delta_e: float = Field(6, ge=0.5, le=40)
    min_area: int = Field(45, ge=4, le=10000)
    fail_area_percent: float = Field(8, ge=0.1, le=100)
    min_registration: float = Field(0.55, ge=0, le=1)
    trim: tuple[int, int, int, int] | None = None
    bleed_px: int = Field(12, ge=0, le=300)
    safe_px: int = Field(24, ge=0, le=300)
    color_regions: list[tuple[int, int, int, int]] = Field(default_factory=list, max_length=30)
    ocr: bool = True
    barcode: bool = True
    ai: bool = False
    @model_validator(mode='after')
    def rectangles(self):
        for r in ([self.trim] if self.trim else []) + self.color_regions:
            if min(r) < 0 or r[2] <= r[0] or r[3] <= r[1]:
                raise ValueError('Rectangles must be x0,y0,x1,y1 with positive dimensions')
        return self

class InspectionRequest(BaseModel):
    reference: str = Field(pattern=r'^[a-f0-9]{32}$')
    candidate: str = Field(pattern=r'^[a-f0-9]{32}$')
    reference_page: int = Field(0, ge=0)
    candidate_page: int = Field(0, ge=0)
    config: Config = Field(default_factory=Config)
