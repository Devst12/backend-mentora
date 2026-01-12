import os
from jose import jwt, JWTError
from fastapi import Header, HTTPException, Depends

SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"


def get_current_user_email(authorization: str = Header(...)):
    try:
        token = authorization.split(" ")[1]
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])

        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")

        return email

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
