import os
import asyncio
import random
import logging
import re
import json
import gc 
import shutil
import tempfile
import time
from typing import List, Optional

import fitz  # PyMuPDF
from rapidocr_onnxruntime import RapidOCR
from dotenv import load_dotenv
from groq import Groq
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.concurrency import run_in_threadpool

load_dotenv()

router = APIRouter(prefix="/mcq", tags=["Quiz Generation"])

# --- CONFIGURATION ---
MAX_FILE_SIZE_MB = 10
MAX_CHARS_PER_CHUNK = 4000
SUMMARY_TIMEOUT = 30.0
ENABLE_OCR = True
MAX_PARALLEL_REQUESTS = 5   
MAX_CONCURRENT_RETRIES = 3 
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_TIMEOUT = 60

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCQGenerator")

MODEL_POOL = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
]

# --- INITIALIZATION ---
try:
    ocr_engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    logger.info("RapidOCR initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize RapidOCR: {e}")
    ocr_engine = None

keys = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3")
]
clients = []
for k in keys:
    if k:
        clients.append(Groq(api_key=k))

if not clients:
    logger.warning("No API Keys found! LLM features will fail.")

# --- UTILITIES ---
class CircuitBreaker:
    def __init__(self):
        self.failures = {}
        self.disabled_until = {}

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
breaker = CircuitBreaker()

def extract_text_with_rapidocr(file_path: str) -> str:
    doc = fitz.open(file_path)
    full_text = []
    try:
        for page_num, page in enumerate(doc):
            page_text = page.get_text()
            full_text.append(f"\n--- Page {page_num + 1} (Text) ---\n{page_text}")

            if ENABLE_OCR and ocr_engine:
                try:
                    image_list = page.get_images(full=True)
                    for i, img in enumerate(image_list):
                        xref = img[0]
                        try:
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            result, _ = ocr_engine(image_bytes)
                            if result:
                                ocr_text_list = [item[1] for item in result]
                                ocr_text = "\n".join(ocr_text_list)
                                if ocr_text.strip():
                                    full_text.append(f"\n--- Page {page_num + 1} (Image {i+1} OCR) ---\n{ocr_text}")
                            del base_image
                            del image_bytes
                        except Exception as img_err:
                            continue
                except Exception:
                    continue
            del page_text
            del page
    finally:
        doc.close()
        gc.collect()
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
        try:
             clean_fixed = fixed.strip().replace("```json", "").replace("```", "")
             json.loads(clean_fixed)
             return clean_fixed
        except:
             raise HTTPException(status_code=500, detail="Failed to generate valid JSON.")

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

async def generate_mcq_logic(chunks: List[str], mcq_count: int = 10) -> str:
    master_summary = await get_master_summary(chunks)
    
    # DYNAMIC PROMPT
    system_prompt = f"""
    You are a JSON API. Generate EXACTLY {mcq_count} DIFFERENT Multiple Choice Questions based on text.
    Constraints: NO MARKDOWN. Only JSON.
    Schema: [{{"question": "str", "options": {{"A":"str","B":"str","C":"str","D":"str"}}, "correct": "A/B/C/D", "hint": "str"}}]
    """
    
    raw_response = await call_llm(f"Text: {master_summary[:8000]}", system_prompt, is_retry=False, temp=0.8)
    return await validate_and_fix_json(raw_response)

def transform_llm_to_game_format(llm_questions: List[dict]) -> List[dict]:
    """
    Converts LLM output format to Game Manager format.
    LLM: {"question": "...", "options": {"A":"...", "B":"..."}, "correct": "A"}
    Game: {"id": "...", "text": "...", "options": ["...", "..."], "correct": "..."}
    """
    from uuid import uuid4
    formatted_questions = []
    for q in llm_questions:
        opts_dict = q.get("options", {})
        opt_values = [opts_dict.get("A"), opts_dict.get("B"), opts_dict.get("C"), opts_dict.get("D")]
        
        correct_key = q.get("correct", "A")
        correct_answer_text = opts_dict.get(correct_key)

        formatted_questions.append({
            "id": str(uuid4()),
            "text": q.get("question"),
            "options": opt_values,
            "correct": correct_answer_text
        })
    return formatted_questions

@router.post("/process_pdf")
async def process_pdf(file: UploadFile = File(...), mode: str = Form(...), mcq_count: int = Form(10)):
    import time 
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_path = tmp_file.name
        try:
            shutil.copyfileobj(file.file, tmp_file)
            tmp_file.close() 

            file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                 raise HTTPException(status_code=400, detail=f"File too large ({file_size_mb:.2f}MB). Limit is {MAX_FILE_SIZE_MB}MB")

            text = await run_in_threadpool(extract_text_with_rapidocr, tmp_path)

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            gc.collect()
    
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text found in PDF.")

    chunks = chunk_text(text, MAX_CHARS_PER_CHUNK)
    
    if mode == "mcq":
        mcq_json_str = await generate_mcq_logic(chunks, mcq_count)
        
        try:
            raw_questions = json.loads(mcq_json_str)
        except Exception as e:
            logger.error(f"JSON Parsing Error: {e}")
            raise HTTPException(status_code=500, detail="Failed to parse LLM generated questions.")
        
        final_questions = transform_llm_to_game_format(raw_questions)
        return {"data": final_questions}
    else:
        return {"data": []}