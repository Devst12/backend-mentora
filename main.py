from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from router.ocr_routes import router as ocr_router
from router.user_routes import router as user_router
from router.mentora_routes import router as mentora_router
from router.uploads_routes import router as uploads_router 


app = FastAPI(title="Unified Backend", version="1.0.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────── MOUNT ROUTES ────────────────────────
app.include_router(ocr_router)
app.include_router(user_router)
app.include_router(mentora_router)
app.include_router(uploads_router) 

# ──────────────────────── ROOT ────────────────────────
@app.get("/")
def read_root():
    return {"status": "running", "services": ["OCR", "UserSync", "MentoraQA", "Uploads"]}

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Unified Server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
