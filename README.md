# SocialSense AI — Backend Architecture

High-performance Python backend powered by **FastAPI**, **PostgreSQL (AsyncPG)**, **SQLAlchemy 2.0**, and **WebSockets** for multi-platform social scraping, keyword-tier billing, and instant real-time dashboard updates.

---

## 1. Architecture Overview

```
[Apify Cloud Scraper Run] 
        ↓ (POST /api/webhooks/apify?keyword_id={id})
[FastAPI Webhook Receiver] 
        ↓ (Async Background Task)
[Apify Dataset Ingestion & Multi-Platform Parser]
        ↓ (TikTok, Instagram, X/Twitter, Reddit, YouTube)
[AI Sentiment & Crisis Classifier (Claude / Gemini)]
        ↓
[PostgreSQL Database (ScrapedData Table)]
        ↓
[WebSocket Manager Broadcast to /ws/{company_id}]
        ↓
[Next.js Dashboard Updates Instantly Without Page Refresh]
```

---

## 2. Setup & Installation

### Step 1: Create Python Virtual Environment
```bash
cd backend
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# macOS / Linux:
source venv/bin/activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure your PostgreSQL database connection string is configured in `.env`:
```env
DATABASE_URL="postgresql+asyncpg://postgres:your_password@localhost:5432/socialsense_db"
APIFY_API_TOKEN="apify_api_your_token_here"
```

### Step 4: Run Database Seed (Optional for Demo Testing)
```bash
python seed.py
```

### Step 5: Start the FastAPI Server
```bash
uvicorn main:app --reload --port 8000
```
API Documentation will be live at:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

---

## 3. Core API Endpoints

### A. Keyword-Based Billing Tier Enforcement
* **`POST /api/companies/{company_id}/keywords`**
  - **Body:**
    ```json
    {
      "keyword_string": "#ApexMotors",
      "platform_flags": {
        "tiktok": true,
        "instagram": true,
        "twitter": true,
        "reddit": true,
        "youtube": true
      }
    }
    ```
  - **Billing Rule:** Counts active keywords for the company. If `active_count >= company.max_keywords`, returns **HTTP 403 Forbidden**:
    ```json
    {
      "detail": "Keyword quota limit reached (5/5 active keywords). Please upgrade your subscription tier to monitor additional keywords."
    }
    ```

### B. Apify Webhook Ingestion
* **`POST /api/webhooks/apify?keyword_id={keyword_uuid}`**
  - Configured in Apify Actor Webhook settings.
  - Downloads raw dataset items (TikTok, Instagram, Twitter, Reddit, YouTube).
  - Automatically classifies sentiment as **Positive**, **Neutral**, **Negative**, or **Crisis**.
  - Inserts records into PostgreSQL `scraped_data` table.
  - Broadcasts payload directly to connected WebSocket clients for that `company_id`.

### C. Real-Time Dashboard WebSocket
* **`ws://localhost:8000/ws/{company_id}`**
  - Clients authenticate and subscribe to real-time events.
  - Receives live JSON events whenever new posts are scraped:
    ```json
    {
      "event": "NEW_SCRAPED_DATA",
      "company_id": "11111111-1111-1111-1111-111111111111",
      "keyword_string": "#ApexMotorsMY",
      "records_count": 24,
      "crisis_count": 2,
      "records": [...]
    }
    ```
