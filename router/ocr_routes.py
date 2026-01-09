# router/ocr_routes.py
import os
import io
import asyncio
import random
import time
import logging
import re
import json
from typing import List, Optional, Dict

import fitz  # PyMuPDF
from rapidocr_onnxruntime import RapidOCR
from dotenv import load_dotenv
from groq import Groq
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.concurrency import run_in_threadpool

# Load env
load_dotenv()

# ────────────── CONFIG ──────────────
MAX_FILE_SIZE_MB = 10
MAX_CHARS_PER_CHUNK = 4000
SUMMARY_TIMEOUT = 30.0
ENABLE_OCR = True
MAX_PARALLEL_REQUESTS = 15
MAX_CONCURRENT_RETRIES = 5
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_TIMEOUT = 60

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RapidOCRBot")

MODEL_POOL = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
]

# ────────────── INIT OCR ──────────────
try:
    ocr_engine = RapidOCR()
    logger.info("RapidOCR initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize RapidOCR: {e}")
    ocr_engine = None

# ────────────── CIRCUIT BREAKER ──────────────
class CircuitBreaker:
    def __init__(self):
        self.failures: Dict[object, int] = {}
        self.disabled_until: Dict[object, float] = {}

    def record_success(self, key):
        self.failures[key] = 0

    def record_failure(self, key):
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] >= CIRCUIT_BREAKER_THRESHOLD:
            logger.warning(f"Circuit Breaker Opened for Key: {key}")
            self.disabled_until[key] = time.time() + CIRCUIT_BREAKER_TIMEOUT

    def is_available(self, key) -> bool:
        if key in self.disabled_until:
            if time.time() < self.disabled_until[key]:
                return False
            else:
                del self.disabled_until[key]
                self.failures[key] = 0
                return True
        return True


request_semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
retry_budget_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RETRIES)

# ────────────── MULTI-KEY INIT ──────────────
clients = []
breaker = CircuitBreaker()

keys = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3")
]

for k in keys:
    if k:
        clients.append(Groq(api_key=k))

if not clients:
    raise ValueError("No API Keys found!")

# ────────────── CREATE ROUTER ──────────────
router = APIRouter()

# ────────────── HELPERS ──────────────
def validate_pdf(file: UploadFile, raw_bytes: bytes):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDFs allowed.")
    size_mb = len(raw_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(status_code=400, detail=f"File too large ({size_mb:.2f}MB).")

def extract_text_with_rapidocr(raw_bytes: bytes) -> str:
    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    full_text = []
    for page_num, page in enumerate(doc):
        # Extract normal text
        page_text = page.get_text()
        full_text.append(f"\n--- Page {page_num + 1} (Text) ---\n{page_text}")

        # OCR
        if ENABLE_OCR and ocr_engine:
            try:
                image_list = page.get_images(full=True)
                for i, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    result, _ = ocr_engine(image_bytes)
                    if result:
                        ocr_text_list = [item[1] for item in result]
                        ocr_text = "\n".join(ocr_text_list)
                        if ocr_text.strip():
                            full_text.append(f"\n--- Page {page_num + 1} (Image {i+1} OCR) ---\n{ocr_text}")
            except Exception as e:
                logger.warning(f"OCR Failed on page {page_num}: {e}")
                continue
    doc.close()
    return "\n".join(full_text)

def chunk_text(text: str, max_chars: int) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        last_dot = chunk.rfind(".")
        if last_dot > 500:
            chunk = chunk[: last_dot + 1]
            end = start + len(chunk)
        chunks.append(chunk.strip())
        start = end
    return chunks

async def call_llm(prompt: str, system_prompt: str, is_retry: bool = False, temp: float = 0.4) -> str:
    client_pool = [c for c in clients if breaker.is_available(c)]
    if not client_pool:
        raise RuntimeError("All API Keys disabled by Circuit Breaker.")
    random.shuffle(client_pool)
    models = MODEL_POOL.copy()
    random.shuffle(models)

    for client in client_pool:
        for model in models:
            if not breaker.is_available(client):
                continue
            sem = retry_budget_semaphore if is_retry else request_semaphore
            try:
                async with sem:
                    response = await run_in_threadpool(
                        client.chat.completions.create,
                        model=model,
                        temperature=temp,
                        timeout=SUMMARY_TIMEOUT,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ]
                    )
                    breaker.record_success(client)
                    return response.choices[0].message.content
            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg or "rate" in error_msg or "quota" in error_msg:
                    breaker.record_failure(client)
                    break
                continue
    raise RuntimeError("All models failed.")

async def safe_summarize_task(chunk: str) -> Optional[str]:
    try:
        prompt = f"Summarize this text concisely:\n\n{chunk}"
        sys_prompt = "You are an expert summarizer. Return only summary."
        return await call_llm(prompt, sys_prompt, is_retry=False)
    except:
        try:
            return await call_llm(prompt, sys_prompt, is_retry=True)
        except:
            return None

async def validate_and_fix_json(json_str: str) -> str:
    clean_str = json_str.strip()
    clean_str = re.sub(r'```json|```', '', clean_str)
    try:
        data = json.loads(clean_str)
        return json.dumps(data)
    except:
        correction_prompt = f"Fix this JSON. Return ONLY valid JSON string.\nBad Output: {json_str}"
        fixed = await call_llm(correction_prompt, "You are a JSON fixer.", is_retry=True, temp=0.1)
        return await validate_and_fix_json(fixed)

async def get_master_summary(chunks: List[str]) -> str:
    sys_prompt = "You are a professional editor. Summarize strictly from text."
    tasks = [safe_summarize_task(chunk) for chunk in chunks]
    results = await asyncio.gather(*tasks)
    valid_summaries = [s for s in results if s]
    if not valid_summaries:
        raise HTTPException(status_code=500, detail="Failed to generate summaries.")
    combined_text = "\n".join(valid_summaries)
    final_prompt = "Combine these notes into one cohesive summary covering entire document:\n" + combined_text
    return await call_llm(final_prompt, sys_prompt, is_retry=False, temp=0.5)

async def generate_mcq_logic(chunks: List[str]) -> str:
    master_summary = await get_master_summary(chunks)
    system_prompt = """
    You are a JSON API. Generate 10 DIFFERENT Multiple Choice Questions based on text.
    Constraints: NO MARKDOWN. Only JSON.
    Schema: [{"question": "str", "options": {"A":"str","B":"str","C":"str","D":"str"}, "correct": "A/B/C/D", "hint": "str"}]
    """
    raw_response = await call_llm(f"Text: {master_summary[:8000]}", system_prompt, is_retry=False, temp=0.8)
    return await validate_and_fix_json(raw_response)

async def generate_summary_logic(chunks: List[str], is_long: bool) -> str:
    tasks = [safe_summarize_task(chunk) for chunk in chunks]
    results = await asyncio.gather(*tasks)
    valid_summaries = [s for s in results if s]
    final_text = "\n".join(valid_summaries)
    mode_instruction = "Write a long detailed summary." if is_long else "Write a concise bullet summary."
    final_prompt = f"{mode_instruction}\n\nBased on:\n{final_text}"
    return await call_llm(final_prompt, "You are a professional editor.", is_retry=False, temp=0.4)

# ────────────── ROUTE ──────────────
@router.post("/process_pdf")
async def process_pdf(file: UploadFile = File(...), mode: str = Form(...)):
    raw_bytes = await file.read()
    validate_pdf(file, raw_bytes)
    text = await run_in_threadpool(extract_text_with_rapidocr, raw_bytes)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text found.")
    chunks = chunk_text(text, MAX_CHARS_PER_CHUNK)
    if mode == "mcq":
        data = await generate_mcq_logic(chunks)
    else:
        is_long = (mode == "summary_long")
        data = await generate_summary_logic(chunks, is_long)
    return {"mode": mode, "data": data}
