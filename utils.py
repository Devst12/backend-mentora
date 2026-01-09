# utils.py
import re
from fastapi import Request, HTTPException

# Generate slug like your Next.js helper
def generate_custom_slug(title: str, id: str) -> str:
    slug_base = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    return f"{slug_base}-{id}"

# Dummy session extractor (replace with JWT/session logic)
async def get_current_user(request: Request):
    # Here, implement your auth system, e.g., JWT from headers
    token = request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Example: decode token and return dict with at least {"email": "..."}
    return {"email": "user@example.com"}  # <-- Replace with real auth
