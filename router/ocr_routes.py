import os
import asyncio
import random
import time
import logging
import re
import json
import gc 
import shutil
import tempfile
from typing import List, Optional, Dict

import fitz  # PyMuPDF
from rapidocr_onnxruntime import RapidOCR
from dotenv import load_dotenv
from groq import Groq
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

# Load env
load_dotenv()

MAX_FILE_SIZE_MB = 10
MAX_CHARS_PER_CHUNK = 8000 # Increased to reduce chunks/API calls
SUMMARY_TIMEOUT = 60.0
ENABLE_OCR = True
MAX_PARALLEL_REQUESTS = 3 # Reduced to save server resources
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_TIMEOUT = 60

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RapidOCRBot")

MODEL_POOL = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
]

try:
    # Optimize OCR engine settings
    ocr_engine = RapidOCR()
    logger.info("RapidOCR initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize RapidOCR: {e}")
    ocr_engine = None

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
breaker = CircuitBreaker()

keys = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3")
]

clients = [Groq(api_key=k) for k in keys if k]
if not clients:
    logger.warning("No API Keys found! LLM features will fail.")
  
router = APIRouter()

def extract_text_with_rapidocr(file_path: str) -> str:
    doc = fitz.open(file_path)
    full_text = []
    
    try:
        for page_num, page in enumerate(doc):
            page_text = page.get_text()
            full_text.append(f"\n--- Page {page_num + 1} ---\n{page_text}")

            if ENABLE_OCR and ocr_engine:
                try:                   
                    image_list = page.get_images(full=True)
                    for i, img in enumerate(image_list):
                        xref = img[0]
                        try:
                            # BUG FIX: Unpack tuple correctly (filename, image_bytes)
                            _, image_bytes = doc.extract_image(xref)
                            
                            result, _ = ocr_engine(image_bytes)
                            
                            if result:
                                ocr_text_list = [item[1] for item in result]
                                ocr_text = "\n".join(ocr_text_list)
                                if ocr_text.strip():
                                    full_text.append(f"\n[OCR Image {i+1}]\n{ocr_text}")
                        except Exception as img_err:
                            logger.warning(f"Failed to OCR image {i}: {img_err}")
                            continue
                except Exception as e:
                    logger.warning(f"OCR Failed on page {page_num}: {e}")
                    continue
        doc.close()
    finally:
        gc.collect()

    return "\n".join(full_text)

def chunk_text(text: str, max_chars: int) -> List[str]:
    """Smart chunking to respect API limits (128k context)"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end
    return chunks

async def call_llm(prompt: str, system_prompt: str, is_retry: bool = False, temp: float = 0.4) -> str:
    if not clients:
         raise HTTPException(status_code=500, detail="LLM configuration error: No API keys.")

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
            sem = request_semaphore 
            try:
                async with sem:
                    # Non-blocking Threadpool execution for I/O
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
                    break # Try next client
                continue
    raise RuntimeError("All models failed.")

async def safe_llm_request(prompt: str, sys_prompt: str) -> Optional[str]:
    try:
        return await call_llm(prompt, sys_prompt, is_retry=False)
    except Exception as e:
        logger.warning(f"LLM Request Failed (Retrying): {e}")
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
        try:
             clean_fixed = fixed.strip().replace("```json", "").replace("```", "")
             json.loads(clean_fixed)
             return clean_fixed
        except:
             raise HTTPException(status_code=500, detail="Failed to generate valid JSON.")

async def generate_mcq_logic(chunks: List[str]) -> str:
    """
    OPTIMIZED: 
    Instead of summarizing every chunk (10+ API calls),
    We pass a summarized version of the full text to the LLM.
    This reduces load significantly.
    """
    
    # 1. Create a condensed summary of the whole document (1 API call)
    combined_text = " ".join(chunks)
    if len(combined_text) > 20000:
        # If massive, just use the first part for context (Safety)
        combined_text = combined_text[:20000]
        
    summary_prompt = f"Summarize this text for MCQ generation:\n{combined_text}"
    master_summary = await safe_llm_request(summary_prompt, "You are a helpful assistant.")

    if not master_summary:
        raise HTTPException(status_code=500, detail="Failed to summarize content.")

    # 2. Generate MCQs from the Summary (1 API call)
    system_prompt = """
    You are a JSON API. Generate 10 DIFFERENT Multiple Choice Questions based on text.
    Constraints: NO MARKDOWN. Only JSON.
    Schema: [{"question": "str", "options": {"A":"str","B":"str","C":"str","D":"str"}, "correct": "A/B/C/D", "hint": "str"}]
    """
    mcq_prompt = f"Based on this summary, generate 10 questions:\n{master_summary[:8000]}"
    raw_response = await call_llm(mcq_prompt, system_prompt, is_retry=False, temp=0.8)
    
    return await validate_and_fix_json(raw_response)

async def generate_summary_logic(chunks: List[str], is_long: bool) -> str:
    combined_text = " ".join(chunks)
    mode_instruction = "Write a long detailed summary." if is_long else "Write a concise bullet summary."
    
    # Only 1 API Call for summary
    final_prompt = f"{mode_instruction}\n\nText:\n{combined_text[:12000]}"
    return await safe_llm_request(final_prompt, "You are a professional editor.")

@router.post("/process_pdf")
async def process_pdf(file: UploadFile = File(...), mode: str = Form(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    
    # 1. Save Temp File
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_path = tmp_file.name
        try:
            shutil.copyfileobj(file.file, tmp_path)
            tmp_file.close() 

            # File Validation
            file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                 raise HTTPException(status_code=400, detail=f"File too large ({file_size_mb:.2f}MB). Limit is {MAX_FILE_SIZE_MB}MB")

            # 2. Extract Text (Threadpool - Non-Blocking)
            text = await run_in_threadpool(extract_text_with_rapidocr, tmp_path)

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            gc.collect()
    
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text found in PDF.")

    # 3. Chunk Text
    chunks = chunk_text(text, MAX_CHARS_PER_CHUNK)
    
    # 4. Process Logic
    try:
        if mode == "mcq":
            data = await generate_mcq_logic(chunks)
        else:
            is_long = (mode == "summary_long")
            data = await generate_summary_logic(chunks, is_long)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process document due to server error.")
    
    return {"mode": mode, "data": data}