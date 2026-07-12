import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from claude_agent import generate_standard, generate_with_mcp_tools
from knowledge_base import KnowledgeBase

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
LOG_SHEET_URL = os.environ.get("LOG_SHEET_URL")
KNOWLEDGE_BASE_CSV = os.environ.get("KNOWLEDGE_BASE_CSV", "knowledge_base_11_columns.csv")
KNOWLEDGE_TOP_K = int(os.environ.get("KNOWLEDGE_TOP_K", "6"))
SAMAAJDATA_MCP_URL = os.environ.get("SAMAAJDATA_MCP_URL", "https://mcp.samaajdata.org/sse")

# Latest merged instructions (Option 1 from main + mentoring/KB flows from update_assistant_prompt)
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
- INTERNAL KNOWLEDGE: You have access to the Samaajadata Knowledge Base (verified golden-standard civic missions). Refer to it as your 'Internal Data Library'.
- When search results are provided with a user message, use ONLY those missions for recommendations. Never invent missions, officials, or scripts.
- Use real mission titles and story context from the library. Never mention CSV files, search results, retrieval, or file names.
- If no mission fits, say you do not have a verified mission for this yet and guide the user on how to investigate locally.
- If information is missing, guide the user on how to FIND it locally.

ENTRY POINT HANDLING:
1. "Know about Samaajadata Collective": Provide a warm and detailed overview of Samaaj Data and the Collective based on the following information:
    - **Purpose**: Samaaj Data exists to serve citizens who act (mapping potholes, garbage, floods, etc.) by providing data, community, and solutions.
    - **What We Do**: Build infrastructure for problem-solving (crowdsourcing waste/water/air data, open tools, connecting organizations, documenting solutions).
    - **The Collective**: A network of organizations (founding partners, contributing partners, community members) unlocking data silos to drive systemic change.
    - **Roots**: An initiative of Reap Benefit, growing out of a decade of changemaking by the Solve Ninja movement.
    - **Principles**: Community is the moat, "Wikipedia, not Encyclopedia" (living resource), amplification over storage, and building in public.
    - Encourage users to join by contributing data or crafting narratives.
2. "Get Insights from Local Data": Initiate the **Local Data Insights Flow** — a data dashboard experience, NOT mentoring or missions.
    - Pull live civic data from SamaajData (counts, locations, trends, charts).
    - Lead with a headline stat, then bullets, then a chart when the data supports it.
    - Offer one follow-up to go deeper — never dump action blueprints or mission steps here.
    - Never mention MCP, tools, or APIs — say you pulled this from **SamaajData Collective**.
3. "I have an idea and need mentoring": Initiate the **Mentoring Intake Flow**. 
    - **PRIORITY**: Once this flow starts, you MUST collect all 5 pieces of information before suggesting any library missions or "Next Steps". Do NOT pivot to mission-matching until the user has confirmed the summary.
    - **CONVERSATIONAL MANDATE**: Do NOT use step numbers or labels. Ask exactly **ONE question** at a time. Keep preambles extremely brief.
    - **Required Details (Collect one-by-one)**:
        1. The problem discovered.
        2. Why it's a personal problem.
        3. The solution idea.
        4. Any testing or progress.
        5. Specific help needed from a mentor.
    - **Recap**: ONLY after all 5 details are collected, provide a structured summary and ask: "Does this look right? Once you confirm, I'll send this to our mentor team."
    - **Final Promise**: After confirmation, provide the 48-hour promise.
4. "I have a problem need solutions": This is your core problem-solving flow. Use your knowledge base to find relevant civic solutions and data-driven missions.
""".strip()

if not ANTHROPIC_API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. The assistant will not function.")

client = Anthropic(api_key=ANTHROPIC_API_KEY or "missing")

knowledge_base = KnowledgeBase(
    Path(__file__).parent / KNOWLEDGE_BASE_CSV,
    top_k=KNOWLEDGE_TOP_K,
)
try:
    knowledge_base.load()
    print(
        f"📚 Loaded {knowledge_base.mission_count} missions from {KNOWLEDGE_BASE_CSV}"
    )
except Exception as exc:
    print(f"WARNING: Knowledge base failed to load: {exc}")

app = FastAPI(title="Socratic Civic Mentor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (Phase 1 — replace with KV/Redis before production deploy)
THREADS: Dict[str, List[Dict[str, Any]]] = {}
THREAD_MODES: Dict[str, str] = {}
LATEST_RESPONSES: Dict[str, str] = {}
RUN_PROGRESS: Dict[str, str] = {}

# -----------------------------------------------------------------------------
# MODELS
# -----------------------------------------------------------------------------
class ChatMessage(BaseModel):
    content: str
    flow_mode: Optional[str] = None

class ThreadResponse(BaseModel):
    thread_id: str

class RunResponse(BaseModel):
    thread_id: str
    run_id: str
    async_mode: bool = False

class LogEntry(BaseModel):
    thread_id: str
    user_query: str
    bot_response: str

# -----------------------------------------------------------------------------
# API ROUTES
# -----------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "api_key_set": bool(ANTHROPIC_API_KEY),
        "provider": "claude",
        "model": CLAUDE_MODEL,
        "knowledge_base_loaded": knowledge_base.loaded,
        "mission_count": knowledge_base.mission_count,
        "knowledge_base_csv": KNOWLEDGE_BASE_CSV,
        "mcp_url": SAMAAJDATA_MCP_URL,
    }

@app.post("/threads", response_model=ThreadResponse)
def create_thread():
    """Create a new chat session."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is missing in environment variables.",
        )
    thread_id = str(uuid.uuid4())
    THREADS[thread_id] = []
    THREAD_MODES[thread_id] = "general"
    print(f"🧵 Created new thread: {thread_id}")
    return {"thread_id": thread_id}

async def _process_insights_message(
    thread_id: str,
    run_id: str,
    user_text: str,
) -> None:
    def on_progress(message: str) -> None:
        RUN_PROGRESS[run_id] = message

    try:
        assistant_text, _response_id = await generate_with_mcp_tools(
            client,
            model=CLAUDE_MODEL,
            system=NEW_INSTRUCTIONS,
            history=THREADS[thread_id],
            user_text=user_text,
            mcp_url=SAMAAJDATA_MCP_URL,
            on_progress=on_progress,
        )
        THREADS[thread_id].append({"role": "assistant", "content": assistant_text})
        LATEST_RESPONSES[run_id] = "completed"
        print(f"🏃 Completed insights response {run_id} for thread {thread_id}")
    except Exception as exc:
        THREADS[thread_id].append(
            {
                "role": "assistant",
                "content": (
                    "I couldn't pull that data right now — SamaajData may not have "
                    "records for that exact city and topic.\n\n"
                    "Try a quick-start chip (e.g. **Waste · Bangalore**) or ask about "
                    "a different city."
                ),
            }
        )
        LATEST_RESPONSES[run_id] = "failed"
        print(f"❌ Insights error for {run_id}: {exc}")
    finally:
        RUN_PROGRESS.pop(run_id, None)

@app.post("/threads/{thread_id}/messages", response_model=RunResponse)
async def post_message(
    thread_id: str,
    msg: ChatMessage,
    background_tasks: BackgroundTasks,
):
    """Post a message and get a Claude response."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is missing.")
    if thread_id not in THREADS:
        raise HTTPException(
            status_code=404,
            detail="Thread not found. Please reset your conversation.",
        )

    if msg.flow_mode:
        THREAD_MODES[thread_id] = msg.flow_mode

    flow_mode = THREAD_MODES.get(thread_id, "general")
    use_mcp = flow_mode == "insights"
    use_knowledge_base = flow_mode == "solutions"

    try:
        THREADS[thread_id].append({"role": "user", "content": msg.content})

        search_blocks = (
            knowledge_base.build_search_result_blocks(msg.content)
            if knowledge_base.loaded and use_knowledge_base
            else []
        )

        if use_mcp:
            run_id = f"run_{uuid.uuid4().hex}"
            LATEST_RESPONSES[run_id] = "in_progress"
            RUN_PROGRESS[run_id] = "Connecting to SamaajData..."
            background_tasks.add_task(
                _process_insights_message,
                thread_id,
                run_id,
                msg.content,
            )
            print(f"🏃 Started insights job {run_id} for thread {thread_id}")
            return {
                "thread_id": thread_id,
                "run_id": run_id,
                "async_mode": True,
            }

        assistant_text, run_id = await generate_standard(
            client,
            model=CLAUDE_MODEL,
            system=NEW_INSTRUCTIONS,
            history=THREADS[thread_id],
            user_text=msg.content,
            search_blocks=search_blocks,
        )

        THREADS[thread_id].append({"role": "assistant", "content": assistant_text})
        LATEST_RESPONSES[run_id] = "completed"

        print(f"🏃 Completed response {run_id} for thread {thread_id} ({flow_mode})")
        return {"thread_id": thread_id, "run_id": run_id}
    except Exception as e:
        THREADS[thread_id].pop()
        print(f"❌ Error getting response: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/threads/{thread_id}/runs/{run_id}")
def check_run_status(thread_id: str, run_id: str):
    """Poll for the completion of a response."""
    status = LATEST_RESPONSES.get(run_id, "in_progress")
    payload = {"status": status}
    if run_id in RUN_PROGRESS:
        payload["progress"] = RUN_PROGRESS[run_id]
    return payload

@app.get("/threads/{thread_id}/messages")
def get_messages(thread_id: str):
    """Fetch messages from the conversation."""
    if thread_id not in THREADS:
        raise HTTPException(
            status_code=404,
            detail="Thread not found. Please reset your conversation.",
        )
    return {"messages": THREADS[thread_id]}

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
except Exception:
    pass

@app.get("/", response_class=HTMLResponse)
def serve_index():
    try:
        index_path = Path(__file__).parent / "static" / "index.html"
        if index_path.exists():
            # Prevent the browser from serving a stale cached page so frontend
            # changes always take effect on reload.
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            return HTMLResponse(content=index_path.read_text(), headers=headers)
        return "<h1>Static index.html not found.</h1>"
    except Exception as e:
        return f"<h1>Server Error</h1><p>{str(e)}</p>"
