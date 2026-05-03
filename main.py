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
    """Create a new session (Conversation) for a student."""
    try:
        # Migrated from client.beta.threads.create() to client.conversations.create()
        conv = client.conversations.create(metadata={"app": "citizen_data_assistant"})
        print(f"🧵 Created new conversation: {conv.id}")
        return {"thread_id": conv.id}
    except Exception as e:
        print(f"❌ Error creating conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Temporary store for Responses results to maintain polling compatibility
LATEST_RESPONSES = {}

@app.post("/threads/{thread_id}/messages", response_model=RunResponse)
def post_message(thread_id: str, msg: ChatMessage):
    """Post a message and get a Response (migrated from Assistants Run)."""
    try:
        # Retrieve the instructions from update_assistant_prompt
        from update_assistant_prompt import NEW_INSTRUCTIONS
        vector_store_id = os.environ.get("VECTOR_STORE_ID")

        if not vector_store_id:
            print("❌ Error: VECTOR_STORE_ID not found in environment.")
            raise HTTPException(status_code=500, detail="VECTOR_STORE_ID not configured")
        
        response = client.responses.create(
            model="gpt-4o",
            conversation={"id": thread_id},
            store=True,
            instructions=NEW_INSTRUCTIONS,
            tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
            input=msg.content
        )
        
        # Store the response for the polling endpoint to find
        run_id = response.id
        LATEST_RESPONSES[run_id] = response
        
        print(f"🏃 Completed response {run_id} for conversation {thread_id}")
        return {"thread_id": thread_id, "run_id": run_id}
    except Exception as e:
        print(f"❌ Error getting response: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/threads/{thread_id}/runs/{run_id}")
def check_run_status(thread_id: str, run_id: str):
    """Poll for the completion of a response (Migrated)."""
    if run_id in LATEST_RESPONSES:
        return {"status": LATEST_RESPONSES[run_id].status}
    return {"status": "in_progress"}

@app.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str):
    """Fetch the latest messages from the conversation (Migrated)."""
    try:
        # In the new API, we can get items from the conversation
        items = client.conversations.items.list(conversation_id=thread_id)
        
        messages = []
        for item in items.data:
            if item.type == 'message':
                messages.append({
                    "role": item.role,
                    "content": item.content[0].text if item.content else ""
                })
        
        # The frontend expects them in chronological order
        return {"messages": messages[::-1]}
    except Exception as e:
        print(f"❌ Error fetching messages: {e}")
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
