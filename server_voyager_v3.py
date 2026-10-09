import time
import requests
import json
import os
import statistics
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List
from laya import Router

# --- CONFIGURACIÓN DEL MOTOR DE DECISIÓN PURA ---
DECISION_ENGINE = "LAYA"  # "LAYA" o "KEV"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

CONFIDENCE_THRESHOLD = 0.43
ARCHITECTURE_TAG = f"Pure_Decision_{DECISION_ENGINE}_Sequential_TH{CONFIDENCE_THRESHOLD}"

current_test_decisions = []
known_useful_entities = set()   
known_useless_entities = set()  
router = None

# Variables globales para la Memoria de Frustración
last_inventory_state = []
blocked_interaction_target = None

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
    global router
    if DECISION_ENGINE == "LAYA":
        print(f"\n[Inicialización] Cargando {DECISION_ENGINE} AI en memoria RAM (CPU)...")
        router = Router(preload=True, device="cpu")
        print(f"[OK] {DECISION_ENGINE} listo. Umbral configurado en: {CONFIDENCE_THRESHOLD}")
    elif DECISION_ENGINE == "KEV":
        print(f"\n[Inicialización] Usando servidor API {DECISION_ENGINE}. Umbral configurado en: {CONFIDENCE_THRESHOLD}")
    yield
    print("\n[Apagado] Cerrando servidor...")

app = FastAPI(title="Voyager Pure Decision Planner (Anti-Loop)", lifespan=lifespan)

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, known_useful_entities, known_useless_entities
    global last_inventory_state, blocked_interaction_target
    current_test_decisions = []
    known_useful_entities = set()
    known_useless_entities = set()
    last_inventory_state = []
    blocked_interaction_target = None
    return {"status": "cleared"}

@app.post("/plan_action")
async def plan_action(state: VoyagerState):
    global last_inventory_state, blocked_interaction_target
    start_time = time.time()
    
    # =====================================================================
    # 0. MEMORIA COGNITIVA DE FRUSTRACIÓN (ANTI-BUCLE)
    # =====================================================================
    if current_test_decisions:
        last_dec = current_test_decisions[-1]["decision"]
        if "->" in last_dec:
            prev_tgt = last_dec.split("->")[1].strip()
            # Si intentó interactuar pero el inventario no cambió, la interacción falló.
            if state.inventory == last_inventory_state:
                blocked_interaction_target = prev_tgt
                print(f"<color=red>[Anti-Loop] Interacción con {prev_tgt} no produjo cambios. Bloqueado temporalmente.</color>")
                
    # Si el inventario cambió, el agente aprendió/consiguió algo nuevo. Reseteamos los bloqueos.
    if state.inventory != last_inventory_state:
        blocked_interaction_target = None
        
    last_inventory_state = list(state.inventory)
    
    inventory_str = ", ".join(state.inventory) if state.inventory else "Empty"
    new_entities = [e for e in state.known_entities if e.id not in known_useful_entities and e.id not in known_useless_entities]
    thought_prefix = ""
    
    # =====================================================================
    # CONSULTA 1: EVALUACIÓN DE RELEVANCIA
    # =====================================================================
    if new_entities:
        latest_entity = new_entities[-1]
        
        eval_state_q1 = {
            "mission": state.mission,
            "new_entity": f"{latest_entity.type}: {latest_entity.description}"
        }
        
        q1 = {
            "is_useful": {
                "type": "choice",
                "instructions": f"Evaluate the newly discovered entity '{latest_entity.type}'. Does it help with the mission?",
                "criteria": {
                    "USEFUL": "Yes, it is a tool, data, server, or objective required for the mission.",
                    "USELESS": "No, it is rusted metal, scrap, or useless noise."
                }
            }
        }
        
        try:
            if DECISION_ENGINE == "LAYA":
                res_q1 = router.predict(eval_state_q1, q1)
            elif DECISION_ENGINE == "KEV":
                payload_q1 = {"state": str(eval_state_q1), "model": "kev-0.8b", "questions": q1}
                res_q1 = requests.post(KEV_URL, json=payload_q1).json()
                
            is_useful_choice = res_q1["answers"]["is_useful"]["choice"]
            q1_confidence = res_q1["answers"]["is_useful"].get("confidence", 0.0)
            
            if is_useful_choice == "USELESS":
                # Chatarra detectada: Recupera la acción anterior exacta
                known_useless_entities.add(latest_entity.id)
                latency = round(time.time() - start_time, 2)
                
                prev_action, prev_target = "EXPLORE", "NONE"
                if current_test_decisions:
                    last_decision_str = current_test_decisions[-1]["decision"]
                    if "->" in last_decision_str:
                        prev_action = "INTERACT"
                        prev_target = last_decision_str.split("->")[1].strip()
                        # Verificamos que el objetivo anterior siga existiendo
                        if not any(e.id == prev_target for e in state.known_entities):
                            prev_action, prev_target = "EXPLORE", "NONE"

                thought = f"[{DECISION_ENGINE}] Evaluó {latest_entity.type} como INÚTIL (Certeza: {q1_confidence:.2f}). Retoma acción: {prev_action}."
                decision_str = f"{prev_action} -> {prev_target}" if prev_action == "INTERACT" else "EXPLORE"
                
                current_test_decisions.append({
                    "pensamiento": thought, "latencia": latency, "decision": decision_str,
                    "error_chatarra": 0, "error_servidor_prematuro": 0, "anulado_por_baja_confianza": 0
                })
                return {"thought": thought, "action": prev_action, "target_id": prev_target, "latency_seconds": latency}
            else:
                known_useful_entities.add(latest_entity.id)
                thought_prefix = f"Validó {latest_entity.type} como útil. "
                
        except Exception as e:
            print(f"[{DECISION_ENGINE} Error Q1] Falló la inferencia: {e}")

    # =====================================================================
    # CONSULTA 2: TOMA DE DECISIÓN DE ACCIÓN
    # =====================================================================
    
    # Filtro Estricto: Excluimos la chatarra Y el objeto actualmente bloqueado por fallar
    valid_entities = [e for e in state.known_entities if e.id in known_useful_entities and e.id != blocked_interaction_target]
    
    if not valid_entities:
        latency = round(time.time() - start_time, 2)
        thought = f"[{DECISION_ENGINE}] {thought_prefix}Entidades filtradas o bloqueadas. Explorando."
        current_test_decisions.append({
            "pensamiento": thought, "latencia": latency, "decision": "EXPLORE",
            "error_chatarra": 0, "error_servidor_prematuro": 0, "anulado_por_baja_confianza": 0
        })
        return {"thought": thought, "action": "EXPLORE", "target_id": "NONE", "latency_seconds": latency}

    entities_str = "\n".join([f"- {e.id} ({e.type}): {e.description}" for e in valid_entities])
    
    eval_state_q2 = {
        "mission": state.mission,
        "inventory": inventory_str,
        "useful_entities": entities_str,
    }
    
    decision_criteria = {
        "EXPLORE": "Explore to find missing items. Select this action if your INVENTORY lacks the exact requirements for the known useful entities."
    }
    
    for e in valid_entities:
        decision_criteria[e.id] = f"Interact with {e.type}: {e.description}. Choose this ONLY if you have the requirements."

    q2 = {
        "next_action": {
            "type": "choice",
            "instructions": "Use the known entities to complete your mission. If you lack the required items, explore. Decide which entity you will interact with.",
            "criteria": decision_criteria
        }
    }
    
    override_triggered = 0
    
    try:
        if DECISION_ENGINE == "LAYA":
            res_q2 = router.predict(eval_state_q2, q2)
        elif DECISION_ENGINE == "KEV":
            payload_q2 = {"state": str(eval_state_q2), "model": "kev-0.8b", "questions": q2}
            res_q2 = requests.post(KEV_URL, json=payload_q2).json()
            
        best_choice = res_q2["answers"]["next_action"]["choice"]
        confidence = res_q2["answers"]["next_action"].get("confidence", 0.0)
        
        if best_choice != "EXPLORE" and confidence < CONFIDENCE_THRESHOLD:
            action = "EXPLORE"
            target_id = "NONE"
            override_triggered = 1
            thought = f"[{DECISION_ENGINE}] {thought_prefix}Quería interactuar con {best_choice}, pero su certeza ({confidence:.2f}) no superó el umbral de {CONFIDENCE_THRESHOLD}. Acción forzada a EXPLORE."
        else:
            if best_choice == "EXPLORE":
                action, target_id = "EXPLORE", "NONE"
                thought = f"[{DECISION_ENGINE}] {thought_prefix}Decidió explorar. (Certeza: {confidence:.2f})"
            else:
                action, target_id = "INTERACT", best_choice
                thought = f"[{DECISION_ENGINE}] {thought_prefix}Decidió interactuar con {target_id}. (Certeza: {confidence:.2f})"
            
    except Exception as e:
        print(f"[{DECISION_ENGINE} Error Q2] Falló la inferencia: {e}")
        action, target_id = "EXPLORE", "NONE"
        thought = f"Error conectando a {DECISION_ENGINE} en Q2. Default a EXPLORE."

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
        "error_servidor_prematuro": err_server,
        "anulado_por_baja_confianza": override_triggered
    })

    return {"thought": thought, "action": action, "target_id": target_id, "latency_seconds": latency}

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions
    filename = "./versiones/Voyager_Architecture_v3.json"

    latencias = [d["latencia"] for d in current_test_decisions]
    latencia_prom = round(statistics.mean(latencias), 4) if latencias else 0.0
    err_scrap = sum(d.get("error_chatarra", 0) for d in current_test_decisions)
    err_server = sum(d.get("error_servidor_prematuro", 0) for d in current_test_decisions)
    overrides_totales = sum(d.get("anulado_por_baja_confianza", 0) for d in current_test_decisions)

    new_test = {
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "errores_interaccion_chatarra": err_scrap,
        "errores_servidor_prematuro": err_server,
        "overrides_por_confianza": overrides_totales,
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
            "total_overrides_confianza_global": 0,
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
    model_entry["total_overrides_confianza_global"] = sum(p.get("overrides_por_confianza", 0) for p in model_entry["pruebas"])

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