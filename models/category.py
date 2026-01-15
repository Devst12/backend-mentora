from pydantic import BaseModel, Field

class CategorySchema(BaseModel):
    name: str = Field(..., min_length=1)

    class Config:
        json_schema_extra = {
            "example": {"name": "Mathematics"}
        }