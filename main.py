import os
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import httpx

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
load_dotenv() # Load from .env file
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ASSISTANT_ID = os.environ.get("ASSISTANT_ID")
LOG_SHEET_URL = os.environ.get("LOG_SHEET_URL")

# Guard against missing API key at startup
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY is not set. The assistant will not function.")

client = OpenAI(api_key=OPENAI_API_KEY or "missing")

app = FastAPI(title="Socratic civic Mentor API")

# Enable CORS for all origins (especially useful for local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# MODELS
# -----------------------------------------------------------------------------
class ChatMessage(BaseModel):
    content: str

class ThreadResponse(BaseModel):
    thread_id: str

class RunResponse(BaseModel):
    thread_id: str
    run_id: str

class LogEntry(BaseModel):
    thread_id: str
    user_query: str
    bot_response: str

# -----------------------------------------------------------------------------
# API ROUTES
# -----------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/threads", response_model=ThreadResponse)
def create_thread():
    """Create a new session (Thread) for a student."""
    try:
        thread = client.beta.threads.create()
        return {"thread_id": thread.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/threads/{thread_id}/messages", response_model=RunResponse)
def post_message(thread_id: str, msg: ChatMessage):
    """Post a message to a thread and trigger an Assistant run."""
    try:
        # Add the message
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=msg.content
        )
        # Create a run
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )
        return {"thread_id": thread_id, "run_id": run.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/threads/{thread_id}/runs/{run_id}")
def check_run_status(thread_id: str, run_id: str):
    """Poll for the completion of a run."""
    try:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        return {"status": run.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str):
    """Fetch all messages from the thread (used when run is 'completed')."""
    try:
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        return {"messages": [
            {
                "role": m.role,
                "content": m.content[0].text.value if m.content else ""
            } for m in messages.data
        ][::-1]} # Reverse to get chronological order
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def send_to_sheet(entry: LogEntry):
    """Helper to send log to Google Sheets via Apps Script Hook."""
    if not LOG_SHEET_URL:
        return
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        try:
            await http_client.post(LOG_SHEET_URL, json=entry.dict())
        except Exception as e:
            print(f"Failed to log to Google Sheets: {e}")

@app.post("/log")
async def log_interaction(entry: LogEntry, background_tasks: BackgroundTasks):
    """Endpoint called by frontend to log a completed turn."""
    background_tasks.add_task(send_to_sheet, entry)
    return {"status": "logging_queued"}

# -----------------------------------------------------------------------------
# FRONTEND SERVING
# -----------------------------------------------------------------------------
# Note: Static files should be in the 'static' directory
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except:
    print("Warning: Static directory not found. Skipping static mount.")

@app.get("/", response_class=HTMLResponse)
def serve_index():
    """Serves the main mobile-fist UI."""
    try:
        # Use absolute path resolution for Vercel
        index_path = Path(__file__).parent / "static" / "index.html"
        if index_path.exists():
            return index_path.read_text()
        return "<h1>Project Initialized.</h1><p>Static index.html not found.</p>"
    except Exception as e:
        return f"<h1>Server Error</h1><p>{str(e)}</p>"
