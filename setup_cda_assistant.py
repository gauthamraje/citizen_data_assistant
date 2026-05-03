import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def setup():
    print("🚀 Initializing Citizen Data Assistant Setup...")
    
    # 1. Create Vector Store
    vector_store = client.vector_stores.create(name="CDA_Knowledge_Base")
    print(f"✅ Created Vector Store: {vector_store.id}")
    
    # 2. Create Assistant
    try:
        from update_assistant_prompt import NEW_INSTRUCTIONS
    except ImportError:
        NEW_INSTRUCTIONS = "You are the Citizen Data Assistant."

    assistant = client.beta.assistants.create(
        name="Citizen Data Assistant",
        instructions=NEW_INSTRUCTIONS,
        model="gpt-4o",
        tools=[{"type": "file_search"}],
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}}
    )
    print(f"✅ Created Assistant: {assistant.id}")
    
    # 3. Output values for your .env
    print("\n--- 📝 COPY THESE TO YOUR .env FILE ---")
    print(f"ASSISTANT_ID={assistant.id}")
    print(f"VECTOR_STORE_ID={vector_store.id}")
    print("---------------------------------------\n")

if __name__ == "__main__":
    setup()
