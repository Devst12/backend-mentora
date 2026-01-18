from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Header
from jose import jwt, JWTError
import os
from dotenv import load_dotenv
from utils.cloudinary_upload import upload_image

load_dotenv()

router = APIRouter(prefix="/api", tags=["Uploads"])

SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

if not SECRET:
    raise ValueError("NEXTAUTH_SECRET environment variable is required")

def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    token = parts[1]
    
    if not SECRET:
        raise HTTPException(status_code=500, detail="Server configuration error: NEXTAUTH_SECRET not set")
    
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        return email
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

@router.post("/uploads/editor-image")
async def upload_editor_image(
    file: UploadFile = File(...),
    user_email: str = Depends(get_current_user_email)
):
    """Upload an image for use in rich text editor"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    try:
        result = await upload_image(file, "mentora/editor-images")
        return {
            "url": result["secure_url"],
            "public_id": result["public_id"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
