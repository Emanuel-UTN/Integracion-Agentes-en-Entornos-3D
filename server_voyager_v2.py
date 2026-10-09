import time
import requests
import json
import os
import statistics
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List

# --- CONFIGURACIÓN DE LOS DOS MOTORES ---
OLLAMA_URL = "http://localhost:11434/api/generate"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

MODEL_S1_FAST = "kev-0.8b"       # Sistema 1: Servidor Kev en puerto 8009
MODEL_S2_PLAN = "llama3"     # Sistema 2: Servidor Ollama en puerto 11434 {qwen2.5:3b  qwen2.5:7b  llama3}
ARCHITECTURE_TAG = f"Dual_{MODEL_S1_FAST}+{MODEL_S2_PLAN}"

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n[Inicialización] Precargando Sistema 2 en memoria RAM/VRAM...")
    requests.post(OLLAMA_URL, json={
        "model": MODEL_S2_PLAN, 
        "prompt": "wake up", 
        "keep_alive": -1 
    })
    print(f"[OK] {MODEL_S2_PLAN} cargado y fijado en memoria Ollama.")
    print(f"[Info] Asegúrate de que el servidor Kev ({MODEL_S1_FAST}) esté corriendo en el puerto 8009.")
    print(f"[Inicialización] Arquitectura {ARCHITECTURE_TAG} lista.\n")
    yield
    print("\n[Apagado] Liberando RAM...")
    requests.post(OLLAMA_URL, json={"model": MODEL_S2_PLAN, "keep_alive": 0})

app = FastAPI(title="Voyager Dual-System Planner (Kev + Ollama)", lifespan=lifespan)

# --- SISTEMA 1: FILTRO RÁPIDO CON KEV (Decisión Pura) ---
def system_1_filter(mission: str, entity: EntityDTO) -> bool:
    # Formato nativo de Kev para evaluar texto (state) mediante preguntas (questions)
    payload = {
        "state": f"MISSION: {mission}\nOBJECT FOUND: {entity.id} ({entity.type})\nDESCRIPTION: {entity.description}",
        "model": "kev-latest",
        "questions": {
            "is_relevant": {
                "type": "noul",
                "instructions": "Is this object potentially useful or required for the MISSION? Answer true if it might be an objective, tool, data, or machine. Answer false if it is clearly useless trash, scrap, or noise."
            }
        }
    }
    
    try:
        res = requests.post(KEV_URL, json=payload).json()
        # Kev devuelve una probabilidad (0.0 a 1.0) para respuestas noul
        probability_yes = res["answers"]["is_relevant"]["noul"]
        
        # Umbral de decisión: si la probabilidad de "Sí" es menor al 50%, lo descartamos
        is_relevant = probability_yes > 0.50
        print(f"[Sistema 1 - Kev] Objeto {entity.id} -> Relevante: {is_relevant} (Certeza: {probability_yes:.2f})")
        return is_relevant
    except Exception as e:
        print(f"[Sistema 1 - Error] Falló conexión a Kev. Delegando al Sist 2. Detalles: {e}")
        return True # Fallback: ante la duda o caída del servidor, piensa el Sist 2

# --- SISTEMA 2: PLANIFICADOR DELIBERATIVO (Ollama) ---
def system_2_planner(state: VoyagerState) -> dict:
    inventory_str = ", ".join(state.inventory) if state.inventory else "Empty"
    entities_str = "\n".join([f"- ID: {e.id} | Type: {e.type} | Desc: {e.description}" for e in state.known_entities])

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
    
    payload = {
        "model": MODEL_S2_PLAN,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "temperature": 0.0
    }
    response = requests.post(OLLAMA_URL, json=payload).json()
    return json.loads(response["response"])

# --- ENDPOINTS RESTANTES ---
@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions
    current_test_decisions = []
    return {"status": "cleared"}

@app.post("/plan_action")
async def plan_action(state: VoyagerState):
    start_time = time.time()
    
    newly_seen = state.known_entities[-1] if state.known_entities else None
    bypass_to_s1 = False
    
    # Ruteo Cognitivo: Si vemos algo nuevo y no tenemos ítems clave, filtramos con Kev
    if newly_seen and len(state.inventory) == 0:
        is_useful = system_1_filter(state.mission, newly_seen)
        if not is_useful:
            bypass_to_s1 = True
            action = "EXPLORE"
            target_id = "NONE"
            thought = f"[Sistema 1 - Kev] Descartado: {newly_seen.id} evaluado como ruido/chatarra."

    if not bypass_to_s1:
        try:
            llm_output = system_2_planner(state)
            action = llm_output.get("action", "EXPLORE").strip().upper()
            target_id = llm_output.get("target_id", "NONE").strip().replace("ID: ", "").replace("ID:", "")
            thought = f"[Sistema 2 - {MODEL_S2_PLAN}] {llm_output.get('thought', '')}"
        except Exception as e:
            action, target_id = "EXPLORE", "NONE"
            thought = f"Error en Sistema 2: {e}"

    if action == "EXPLORE":
        target_id = "NONE"

    latency = round(time.time() - start_time, 2)
    decision_str = f"{action} -> {target_id}" if action == "INTERACT" else "EXPLORE"

    target_entity = next((e for e in state.known_entities if e.id == target_id), None)
    err_scrap = 1 if (target_entity and target_entity.type == "Scrap" and action == "INTERACT") else 0
    err_server = 1 if (target_entity and target_entity.type == "MainServer" and "DataModule" not in state.inventory and action == "INTERACT") else 0

    current_test_decisions.append({
        "pensamiento": thought,
        "latencia": latency,
        "decision": decision_str,
        "error_chatarra": err_scrap,
        "error_servidor_prematuro": err_server
    })

    return {"thought": thought, "action": action, "target_id": target_id, "latency_seconds": latency}

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions
    filename = "./versiones/Voyager_Architecture_v2.json"

    latencias = [d["latencia"] for d in current_test_decisions]
    latencia_prom = round(statistics.mean(latencias), 4) if latencias else 0.0
    err_scrap = sum(d.get("error_chatarra", 0) for d in current_test_decisions)
    err_server = sum(d.get("error_servidor_prematuro", 0) for d in current_test_decisions)

    new_test = {
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "errores_interaccion_chatarra": err_scrap,
        "errores_servidor_prematuro": err_server,
        "decisiones": current_test_decisions,
        "latencia_promedio": latencia_prom
    }

    full_data = []
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, "r") as f:
                full_data = json.load(f)
        except json.JSONDecodeError:
            full_data = []

    model_entry = next((item for item in full_data if item["modelo"] == ARCHITECTURE_TAG), None)
    if not model_entry:
        model_entry = {
            "modelo": ARCHITECTURE_TAG,
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
    return {"status": "saved", "architecture": ARCHITECTURE_TAG}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)