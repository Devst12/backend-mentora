#main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from router.ocr_routes import router as ocr_router
from router.user_routes import router as user_router

from router.uploads_routes import router as uploads_router 
from router.mcq_routes import router as mcq_router

from router.category_routes import router as category_router
from router.qa_routes import router as qa_router
from router.badge_routes import router as badge_router
from router.upload_routes import router as upload_router
from router.quiz_routes import router as quiz_router

# from router.quiz_generator import router as generator_router
# from router.game_manager import router as game_router
from router.eco_routes import router as eco_router


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
app.include_router(mcq_router)
app.include_router(uploads_router) 
app.include_router(category_router)
app.include_router(qa_router)
app.include_router(badge_router)
app.include_router(upload_router)
app.include_router(quiz_router)

# app.include_router(generator_router)
# app.include_router(game_router)
app.include_router(eco_router)
# ──────────────────────── ROOT ────────────────────────
@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "running", "services": ["OCR", "UserSync", "MentoraQA", "Uploads"]}

if __name__ == "__main__":
    import uvicorn
    print("Starting Unified Server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)