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
load_dotenv() # Load from .env file (if local)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
VECTOR_STORE_ID = os.environ.get("VECTOR_STORE_ID")
LOG_SHEET_URL = os.environ.get("LOG_SHEET_URL")

# Assistant Instructions (Embedded to avoid import issues on Vercel)
NEW_INSTRUCTIONS = """
You are the Citizen Data Assistant (CDA), a Socratic mentor for citizens in India powered by the Samaajadata Collective. Your goal is to help citizens understand and use local data to solve civic problems.

CORE MENTORING PRINCIPLES:
1. DATA-DRIVEN INSIGHTS: Always encourage users to look for data (observations, photos, official records) to back their civic claims.
2. UNDERSTAND FIRST: Before giving advice, ask one question to understand what data or observations the user already has.
3. STORY-DRIVEN GUIDANCE: Use examples of how other citizens have used data to drive change. 
4. VALIDATE THEN DISCLOSE: Only provide detailed technical or legal steps after the user has shared their context.
5. ZERO-AMBIGUITY MANDATE: Help users find exact official roles and data sources.
6. PLAIN-LANGUAGE PRECISION: Keep conversation warm but technical data terms accurate.
7. SEPARATION OF DETAILS: Keep conversation warm; put technical audit checklists and data schemas after a "---" delimiter.
8. NO CITATIONS: Never include citation markers like 【...†source】.
9. MULTILINGUAL: Detect and mirror user language.

STRICT GUARDRAILS:
- INTERNAL KNOWLEDGE: You have access to the Samaajadata Knowledge Base. Refer to it as your 'Internal Data Library'.
- If information is missing, guide the user on how to FIND it locally.

ENTRY POINT HANDLING:
1. "Know about Samaajadata Collective": Inform the user that we are currently working on this section and it will be available soon. Encourage them to stay tuned! In the meantime, invite them to explore other sections by clicking the **Home button** (top-right) to return to the main menu.
2. "Get Insights from Local Data": Inform the user that we are currently working on this section and it will be available soon. Encourage them to stay tuned! In the meantime, invite them to explore other sections by clicking the **Home button** (top-right) to return to the main menu.
3. "I have an idea and need mentoring": Initiate the **Mentoring Intake Flow**. 
    - **Required Details (Collect one-by-one)**: 1. Problem, 2. Personal impact, 3. Solution idea, 4. Progress, 5. Help needed.
    - Ask exactly ONE question at a time. Do NOT use step numbers.
4. "I have a problem need solutions": Core problem-solving flow. Use your knowledge base to find relevant civic solutions.
""".strip()

# Guard against missing API key at startup
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY is not set. The assistant will not function.")

client = OpenAI(api_key=OPENAI_API_KEY or "missing")

app = FastAPI(title="Socratic Civic Mentor API")

# Enable CORS
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
    return {"status": "ok", "api_key_set": bool(OPENAI_API_KEY), "vs_id_set": bool(VECTOR_STORE_ID)}

@app.post("/threads", response_model=ThreadResponse)
def create_thread():
    """Create a new session (Conversation) for a student."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is missing in environment variables.")
    try:
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
    """Post a message and get a Response."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is missing.")
    if not VECTOR_STORE_ID:
        raise HTTPException(status_code=500, detail="VECTOR_STORE_ID is missing.")

    try:
        response = client.responses.create(
            model="gpt-4o",
            conversation={"id": thread_id},
            store=True,
            instructions=NEW_INSTRUCTIONS,
            tools=[{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}],
            input=msg.content
        )
        
        run_id = response.id
        LATEST_RESPONSES[run_id] = response
        
        print(f"🏃 Completed response {run_id} for conversation {thread_id}")
        return {"thread_id": thread_id, "run_id": run_id}
    except Exception as e:
        print(f"❌ Error getting response: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/threads/{thread_id}/runs/{run_id}")
def check_run_status(thread_id: str, run_id: str):
    """Poll for the completion of a response."""
    if run_id in LATEST_RESPONSES:
        return {"status": LATEST_RESPONSES[run_id].status}
    return {"status": "in_progress"}

@app.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str):
    """Fetch the latest messages from the conversation."""
    try:
        items = client.conversations.items.list(conversation_id=thread_id)
        messages = []
        for item in items.data:
            if item.type == 'message':
                messages.append({
                    "role": item.role,
                    "content": item.content[0].text if item.content else ""
                })
        return {"messages": messages[::-1]}
    except Exception as e:
        print(f"❌ Error fetching messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def send_to_sheet(entry: LogEntry):
    if not LOG_SHEET_URL:
        return
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        try:
            await http_client.post(LOG_SHEET_URL, json=entry.dict())
        except Exception as e:
            print(f"Failed to log to Google Sheets: {e}")

@app.post("/log")
async def log_interaction(entry: LogEntry, background_tasks: BackgroundTasks):
    background_tasks.add_task(send_to_sheet, entry)
    return {"status": "logging_queued"}

# -----------------------------------------------------------------------------
# FRONTEND SERVING
# -----------------------------------------------------------------------------
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except:
    pass

@app.get("/", response_class=HTMLResponse)
def serve_index():
    try:
        index_path = Path(__file__).parent / "static" / "index.html"
        if index_path.exists():
            return index_path.read_text()
        return "<h1>Static index.html not found.</h1>"
    except Exception as e:
        return f"<h1>Server Error</h1><p>{str(e)}</p>"
