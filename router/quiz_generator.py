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
from uuid import uuid4

import fitz  # PyMuPDF
from dotenv import load_dotenv
from groq import Groq
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.concurrency import run_in_threadpool

load_dotenv()

router = APIRouter(prefix="/mcq", tags=["Quiz Generation"])

# --- CONFIGURATION ---
MAX_FILE_SIZE_MB = 10
# Increased chunk size slightly since we aren't summarizing anymore
MAX_CHARS_PER_CHUNK = 8000 
SUMMARY_TIMEOUT = 30.0
MAX_PARALLEL_REQUESTS = 5   
MAX_CONCURRENT_RETRIES = 3 
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_TIMEOUT = 60
# Limit context sent to LLM to avoid token overflow (approx 6k-7k tokens)
MAX_CONTEXT_LENGTH = 25000 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCQGenerator")

MODEL_POOL = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
]

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

def extract_text_from_pdf(file_path: str) -> str:
    """
    Lightweight text extraction. Only reads text layer.
    """
    doc = fitz.open(file_path)
    full_text = []
    try:
        for page_num, page in enumerate(doc):
            page_text = page.get_text()
            full_text.append(f"\n--- Page {page_num + 1} ---\n{page_text}")
            del page
    except Exception as e:
        logger.error(f"Error reading PDF page: {e}")
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

async def validate_and_fix_json(json_str: str) -> str:
    clean_str = json_str.strip()
    clean_str = re.sub(r'```json|```', '', clean_str)
    
    if clean_str.startswith("{") or clean_str.startswith("["):
        try:
            json.loads(clean_str)
            return clean_str
        except:
            pass

    match = re.search(r'(\{.*\}|\[.*\])', clean_str, re.DOTALL)
    if match:
        candidate = match.group(1)
        try:
            json.loads(candidate)
            return candidate
        except:
            pass

    correction_prompt = f"Fix this JSON. Return ONLY valid JSON string.\nBad Output: {json_str}"
    try:
        fixed = await call_llm(correction_prompt, "You are a JSON fixer.", is_retry=True, temp=0.1)
        clean_fixed = fixed.strip().replace("```json", "").replace("```", "")
        json.loads(clean_fixed)
        return clean_fixed
    except:
         raise HTTPException(status_code=500, detail="Failed to generate valid JSON.")

# --- CHANGED: Direct Generation Logic (No Summary) ---
async def generate_mcq_logic(chunks: List[str], mcq_count: int = 10) -> str:
    """
    Directly feeds text chunks to LLM to generate questions.
    skips the summarization step.
    """
    # 1. Join chunks into a single context string
    full_context = "\n".join(chunks)
    
    # 2. Truncate if too long to prevent 502/Token Errors
    # Keeping it under ~25k chars ensures we don't blow up the LLM context window
    if len(full_context) > MAX_CONTEXT_LENGTH:
        logger.warning(f"Text too long ({len(full_context)} chars). Truncating to {MAX_CONTEXT_LENGTH} chars.")
        full_context = full_context[:MAX_CONTEXT_LENGTH] + "...[truncated]"

    # 3. Dynamic Prompt
    system_prompt = f"""
    You are a Quiz Generator API. 
    Task: Generate EXACTLY {mcq_count} DIFFERENT Multiple Choice Questions based on the provided text.
    
    Output Rules:
    1. Return ONLY valid JSON. No markdown formatting.
    2. Format: List of objects.
    3. Schema: 
    [
      {{
        "question": "The question text here?",
        "options": {{
          "A": "Option 1",
          "B": "Option 2",
          "C": "Option 3",
          "D": "Option 4"
        }},
        "correct": "A",
        "hint": "A short hint"
      }}
    ]
    """
    
    user_prompt = f"Here is the text content:\n\n{full_context}\n\nGenerate {mcq_count} MCQs now."
    
    # 4. Call LLM directly
    raw_response = await call_llm(user_prompt, system_prompt, is_retry=False, temp=0.7)
    return await validate_and_fix_json(raw_response)

def transform_llm_to_game_format(llm_questions: List[dict]) -> List[dict]:
    formatted_questions = []
    for q in llm_questions:
        if not isinstance(q, dict):
            continue

        opts_dict = q.get("options", {})
        if not isinstance(opts_dict, dict):
            continue

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
    # Create temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_path = tmp_file.name
        try:
            shutil.copyfileobj(file.file, tmp_file)
            tmp_file.close() 

            file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                 raise HTTPException(status_code=400, detail=f"File too large ({file_size_mb:.2f}MB). Limit is {MAX_FILE_SIZE_MB}MB")

            # Lightweight extraction
            text = await run_in_threadpool(extract_text_from_pdf, tmp_path)

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            gc.collect()
    
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text found in PDF. Scanned PDFs are not supported.")

    chunks = chunk_text(text, MAX_CHARS_PER_CHUNK)
    
    if mode == "mcq":
        # Calls the new direct generation function
        mcq_json_str = await generate_mcq_logic(chunks, mcq_count)
        
        try:
            data = json.loads(mcq_json_str)
            
            if isinstance(data, list):
                raw_questions = data
            elif isinstance(data, dict):
                found_list = False
                for key, value in data.items():
                    if isinstance(value, list):
                        raw_questions = value
                        found_list = True
                        break
                if not found_list:
                    raw_questions = [data]
            else:
                raise ValueError("Parsed JSON is not a list or dictionary.")
                
        except Exception as e:
            logger.error(f"JSON Parsing Error: {e}")
            logger.error(f"Received String: {mcq_json_str[:200]}...")
            raise HTTPException(status_code=500, detail="Failed to parse LLM generated questions.")
        
        final_questions = transform_llm_to_game_format(raw_questions)
        return {"data": final_questions}
    else:
        return {"data": []}