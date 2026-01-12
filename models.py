from pydantic import BaseModel, EmailStr
from typing import List, Optional


class QuestionCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    tags: List[str] = []


class AnswerCreate(BaseModel):
    text: str


class CommentCreate(BaseModel):
    text: str
