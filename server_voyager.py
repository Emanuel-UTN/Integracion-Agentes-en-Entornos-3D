import time
import requests
import json
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Voyager-Style General Planner")

# Configuración de Ollama (Asegúrate de tener Ollama corriendo localmente)
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:3b" # O "phi3", "mistral", el que prefieras

class EntityDTO(BaseModel):
    id: str
    type: str
    description: str
    distance: float

class VoyagerState(BaseModel):
    mission: str
    inventory: List[str]
    known_entities: List[EntityDTO]

@app.post("/plan_action")
async def plan_action(state: VoyagerState):
    start_time = time.time()
    
    # 1. Construcción dinámica del Prompt basado en los datos de Unity
    inventory_str = ", ".join(state.inventory) if state.inventory else "Empty"
    
    entities_str = ""
    for e in state.known_entities:
        entities_str += f"- ID: {e.id} | Type: {e.type} | Distance: {e.distance:.1f}m | Desc: {e.description}\n"
    
    if not entities_str:
        entities_str = "No entities in memory."

    prompt = f"""
    You are an autonomous AI agent in a 3D environment.
    YOUR MISSION: {state.mission}
    
    CURRENT INVENTORY: [{inventory_str}]
    
    KNOWN ENTITIES IN MEMORY:
    {entities_str}
    
    DECISION RULES:
    1. Analyze the descriptions of the known entities to determine if they help you complete your MISSION.
    2. If a known entity is useful and you have the requirements to use it, choose action "INTERACT" and provide its ID.
    3. If you lack requirements or no known entities are useful, choose action "EXPLORE" to find new things (target_id should be "NONE").
    4. You must output ONLY a valid JSON object.
    
    OUTPUT FORMAT:
    {{
        "thought": "your step-by-step reasoning based on descriptions and inventory",
        "action": "INTERACT" or "EXPLORE",
        "target_id": "Entity ID or NONE"
    }}
    """

    # 2. Llamada a Ollama
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "format": "json", # Fuerza a Llama3 a responder en JSON puro
        "stream": False,
        "temperature": 0.1 # Baja temperatura para lógica estricta
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response_data = response.json()
        llm_output = json.loads(response_data["response"])
        
        action = llm_output.get("action", "EXPLORE")
        target_id = llm_output.get("target_id", "NONE")
        thought = llm_output.get("thought", "No thought provided.")
        
    except Exception as e:
        print(f"Error parseando LLM: {e}")
        action = "EXPLORE"
        target_id = "NONE"
        thought = "Error in cognitive process, defaulting to exploration."

    latency = round(time.time() - start_time, 2)
    
    return {
        "thought": thought,
        "action": action,
        "target_id": target_id,
        "latency_seconds": latency
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)