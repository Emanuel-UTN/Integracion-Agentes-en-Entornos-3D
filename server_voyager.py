import time
import requests
import json
import os
import statistics
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Voyager-Style General Planner (Benchmark Mode)")

# Configuración de Ollama (Asegúrate de tener Ollama corriendo localmente)
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.2" # llama3  llava-llama3:8b  qwen2.5:3b  qwen2.5:7b  phi3:3.8b  mistral


# Almacenamiento temporal para la prueba en curso
current_test_decisions = []

class EntityDTO(BaseModel):
    id: str
    type: str
    description: str
    distance: float

class VoyagerState(BaseModel):
    mission: str
    inventory: List[str]
    known_entities: List[EntityDTO]

class BenchmarkEnd(BaseModel):
    duracion_total: float
    timed_out: bool = False

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
        "temperature": 0.0 # Bajamos a 0.0 para hacer al modelo 100% determinista
    }
    
    invalid_scrap_interaction = 0
    premature_server_interaction = 0

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response_data = response.json()
        llm_output = json.loads(response_data["response"])
        
        action = llm_output.get("action", "EXPLORE")
        target_id = llm_output.get("target_id", "NONE")
        thought = llm_output.get("thought", "No thought provided.")

        # Limpiar prefijos alucinados comunes
        if target_id.startswith("ID: "):
            target_id = target_id.replace("ID: ", "")
        if target_id.startswith("ID:"):
            target_id = target_id.replace("ID:", "")

        # --- DETECTOR DE ERRORES LÓGICOS (Añadir antes de guardar en current_test_decisions) ---
        if action == "INTERACT":
            # Buscamos si el objetivo es chatarra
            target_entity = next((e for e in state.known_entities if e.id == target_id), None)
            if target_entity and target_entity.type == "Scrap":
                invalid_scrap_interaction = 1
                print("[⚠️ ERROR LÓGICO] El modelo intentó interactuar con chatarra.")
                
            # Buscamos si el objetivo es el MainServer sin tener el DataModule
            if target_entity and target_entity.type == "MainServer" and "DataModule" not in state.inventory:
                premature_server_interaction = 1
                print("[⚠️️ ERROR LÓGICO] El modelo intentó usar el MainServer sin el DataModule.")
        
    except Exception as e:
        print(f"Error parseando LLM: {e}")
        action = "EXPLORE"
        target_id = "NONE"
        thought = "Error in cognitive process, defaulting to exploration."

    latency = round(time.time() - start_time, 2)
    decision_str = f"{action} -> {target_id}" if action == "INTERACT" else "EXPLORE"

    # Guarda la iteración en la memoria de la prueba actual
    current_test_decisions.append({
        "pensamiento": thought,
        "latencia": latency,
        "decision": decision_str,
        "error_chatarra": invalid_scrap_interaction,
        "error_servidor_prematuro": premature_server_interaction
    })
    
    return {
        "thought": thought,
        "action": action,
        "target_id": target_id,
        "latency_seconds": latency
    }

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions
    current_test_decisions = [] # Limpia la memoria sucia de pruebas canceladas
    return {"status": "started and cleared"}

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions
    filename = f"./versiones/Voyager_Architecture_v1.json"
    
    latencias = [d["latencia"] for d in current_test_decisions]
    latencia_prom = round(statistics.mean(latencias), 4) if latencias else 0.0

    total_errores_chatarra = sum(d.get("error_chatarra", 0) for d in current_test_decisions)
    total_errores_servidor = sum(d.get("error_servidor_prematuro", 0) for d in current_test_decisions)

    new_test = {
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "errores_interaccion_chatarra": total_errores_chatarra,
        "errores_servidor_prematuro": total_errores_servidor,
        "decisiones": current_test_decisions,
        "latencia_promedio": latencia_prom
    }

    # LECTURA SEGURA: Si el archivo no existe o está vacío, creamos la estructura base
    full_data = []
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, "r") as f:
                full_data = json.load(f)
        except json.JSONDecodeError:
            full_data = []
    
    model_entry = next((item for item in full_data if item["modelo"] == MODEL_NAME), None)
    if not model_entry:
        model_entry = {
            "modelo": MODEL_NAME, 
            "pruebas": [], 
            "total_timeouts": 0,
            "total_errores_chatarra_global": 0,
            "total_errores_servidor_global": 0,
            "latencia_promedio": 0, 
            "latencia_maxima": 0, 
            "latencia_mediana": 0, 
            "latencia_minima": 0, 
            "duracion_promedio": 0
        }
        full_data.append(model_entry)

    model_entry["pruebas"].append(new_test)

    # Recalcular estadísticas y contadores globales
    model_entry["total_timeouts"] = sum(1 for p in model_entry["pruebas"] if p.get("supero_limite_tiempo", False))
    model_entry["total_errores_chatarra_global"] = sum(p.get("errores_interaccion_chatarra", 0) for p in model_entry["pruebas"])
    model_entry["total_errores_servidor_global"] = sum(p.get("errores_servidor_prematuro", 0) for p in model_entry["pruebas"])

    all_latencies = [d["latencia"] for p in model_entry["pruebas"] for d in p["decisiones"]] if model_entry["pruebas"] else [0]
    all_durations = [p["duracion_total"] for p in model_entry["pruebas"] if not p.get("supero_limite_tiempo", False)]
    
    model_entry["latencia_promedio"] = round(statistics.mean(all_latencies), 4) if all_latencies else 0
    model_entry["latencia_maxima"] = round(max(all_latencies), 2) if all_latencies else 0
    model_entry["latencia_mediana"] = round(statistics.median(all_latencies), 2) if all_latencies else 0
    model_entry["latencia_minima"] = round(min(all_latencies), 2) if all_latencies else 0
    model_entry["duracion_promedio"] = round(statistics.mean(all_durations), 4) if all_durations else 0

    with open(filename, "w") as f:
        json.dump(full_data, f, indent=4)

    current_test_decisions = []
    return {"status": "saved_with_metrics"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)