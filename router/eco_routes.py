from fastapi import APIRouter
import requests
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create the router
router = APIRouter()

# Get API Key
GOOGLE_API_KEY = os.getenv("PAGESPEED_API_KEY")

@router.get("/audit", tags=["Eco Audit"])
async def audit_site(url: str):
    # 1. Handle URL format
    if not url.startswith("http"):
        target_url = f"https://{url}"
    else:
        target_url = url

    try:
      
        if not GOOGLE_API_KEY:
            return {"status": "error", "message": "Server Error: PAGESPEED_API_KEY is missing in .env"}

        # 3. Call Google API
        api_endpoint = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={target_url}&key={GOOGLE_API_KEY}&category=PERFORMANCE"
        response = requests.get(api_endpoint, timeout=30)
        data = response.json()

        if 'error' in data:
            return {"status": "error", "message": data['error']['message']}

        
        audits = data.get('lighthouseResult', {}).get('audits', {})
        network_items = audits.get('network-requests', {}).get('details', {}).get('items', [])
        
        total_bytes = sum(item.get('transferSize', 0) for item in network_items)
        total_mb = total_bytes / (1024 * 1024)
        
        
        co2_grams = (total_bytes / 1073741824) * 0.81 * 442

       
        if total_mb < 0.5: grade = "A+"
        elif total_mb < 1.0: grade = "A"
        elif total_mb < 2.0: grade = "B"
        elif total_mb < 5.0: grade = "C"
        else: grade = "F"

       
        issues = []
        if total_mb > 2:
            issues.append(f"High Payload: Page size is {round(total_mb, 2)} MB (Sustainable target is < 1 MB).")
        
        heavy_images = [item for item in network_items if item.get('resourceType') == 'image' and item.get('transferSize', 0) > 500000]
        if heavy_images:
            issues.append(f"Unoptimized Images: Found {len(heavy_images)} images over 500KB. Compress them to save energy.")
        
        if len(network_items) > 80:
            issues.append(f"High Request Count: {len(network_items)} server requests detected. Reduce scripts/plugins.")

        
        advice = "Excellent! Your site is energy efficient."
        if total_mb > 5:
            advice = "Critical: This site is heavy. Remove unused JavaScript and compress video/images immediately."
        elif total_mb > 1:
            advice = "Good start, but try using Next.js Image Optimization to reduce bandwidth."

        return {
            "status": "success",
            "url": target_url,
            "page_weight_mb": round(total_mb, 2),
            "co2_emitted_grams": round(co2_grams, 4),
            "grade": grade,
            "issues": issues,
            "advice": advice,
            "total_requests": len(network_items)
        }

    except Exception as e:
        print(f"Eco Audit Error: {e}")
        return {"status": "error", "message": "Could not analyze. Check the URL or API Key."}