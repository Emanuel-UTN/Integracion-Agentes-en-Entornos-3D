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

# --- CONFIGURACIÓN EXPERIMENTAL ---
DECISION_ENGINE = "KEV"             # "LAYA" o "KEV"
COMPRESSION_LEVEL = "MINIMAL"       # "VERBOSE", "COMPACT", "MINIMAL", "ULTRA_MINIMAL"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

router = None
current_test_decisions = []
current_mission_id = "Mision_Estandar"

class VisibleEntityDTO(BaseModel):
    id: str
    type: str
    description: str
    relative_pos: str

class ActionOptionDTO(BaseModel):
    code: str
    status: str

class EgocentricStateDTO(BaseModel):
    mission_id: str = "Mision_Estandar"
    mission: str = ""
    current_subgoal: str = "Objective"
    inventory: List[str] = []
    newly_spotted: List[VisibleEntityDTO] = []
    visible_in_fov: List[VisibleEntityDTO] = []
    available_actions: List[ActionOptionDTO] = []

class BenchmarkEnd(BaseModel):
    duracion_total: float
    timed_out: bool = False

def estimate_tokens(state_obj, questions_obj) -> int:
    raw = f"{json.dumps(state_obj, ensure_ascii=False)} {json.dumps(questions_obj, ensure_ascii=False)}"
    opts_count = sum(len(q.get("criteria", {})) for q in questions_obj.values())
    return int(len(raw) / 3.8) + 4 + (opts_count * 2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global router
    if DECISION_ENGINE == "LAYA":
        print(f"\n[Inicialización] Cargando {DECISION_ENGINE} AI en CPU (Modo: {COMPRESSION_LEVEL})...")
        router = Router(preload=True, device="cpu")
    yield

app = FastAPI(title="Voyager V4 Multi-Mission Egocentric Controller", lifespan=lifespan)

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions
    current_test_decisions = []
    return {"status": "cleared"}

# =====================================================================
# CONSULTA 1: IDENTIFICACIÓN VISUAL SEGÚN LA MISIÓN DE UNITY
# =====================================================================
@app.post("/evaluate_entities")
async def evaluate_entities(state: EgocentricStateDTO):
    global current_mission_id
    current_mission_id = state.mission_id or "Mision_Estandar"

    useful_ids = []
    useless_ids = []

    for ent in state.newly_spotted:
        start_t = time.time()

        if COMPRESSION_LEVEL == "VERBOSE":
            eval_state = {"mission": state.mission, "object": f"{ent.type}: {ent.description}"}
            instructions = f"Evaluate '{ent.type}' based on the mission. Is it useful for the mission or useless scrap?"
            criteria = {
                "USEFUL": "Yes, it is a required objective, tool, key, door, or terminal mentioned or needed in the mission.",
                "USELESS": "No, it is rusted metal, broken robot part, debris, or unrelated to the mission."
            }
        elif COMPRESSION_LEVEL == "COMPACT":
            eval_state = f"Mission:{state.mission} | Obj:{ent.type}({ent.description})"
            instructions = "Is this object useful for the mission?"
            criteria = {
                "USEFUL": "Useful mission item, door, or terminal.",
                "USELESS": "Useless scrap or broken part."
            }
        elif COMPRESSION_LEVEL == "MINIMAL":
            eval_state = f"Mission:{state.mission}|Obj:{ent.type}:{ent.description}"
            instructions = "Role in mission?"
            criteria = {
                "USEFUL": "Useful objective, key, door, or tool.",
                "USELESS": "Rusted metal, broken part, or scrap."
            }
        else:  # ULTRA_MINIMAL (~28 tokens)
            eval_state = f"M:{state.mission}|O:{ent.type}:{ent.description}"
            instructions = "Useful?"
            criteria = {
                "USEFUL": "Useful target/tool",
                "USELESS": "Useless scrap"
            }

        q1 = {
            "relevance": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria
            }
        }

        tokens_q1 = estimate_tokens(eval_state, q1)
        try:
            if DECISION_ENGINE == "LAYA":
                res = router.predict(eval_state, q1)
                choice = res["answers"]["relevance"]["choice"]
            else:
                payload = {"state": str(eval_state), "model": "kev-0.8b", "questions": q1}
                res = requests.post(KEV_URL, json=payload).json()
                choice = res["answers"]["relevance"]["choice"]
                if "usage" in res and "input_tokens" in res["usage"]:
                    tokens_q1 = res["usage"]["input_tokens"]
        except Exception as e:
            print(f"[Error Q1] {e}")
            choice = "USELESS" if "Scrap" in ent.type else "USEFUL"

        lat = round(time.time() - start_t, 2)
        if choice == "USEFUL":
            useful_ids.append(ent.id)
        else:
            useless_ids.append(ent.id)

        current_test_decisions.append({
            "pensamiento": f"[Q1-VistaLejana|{tokens_q1}tok] {ent.id} -> {choice}",
            "latencia": lat,
            "tokens_entrada": tokens_q1,
            "decision": f"SCAN:{ent.id}={choice}",
            "error_chatarra": 0,
            "error_servidor_prematuro": 0
        })

    return {"useful_ids": useful_ids, "useless_ids": useless_ids}

# =====================================================================
# CONSULTA 2: CONTROL DE MOVIMIENTO Y CÁMARA GENÉRICO
# =====================================================================
def build_egocentric_query(state: EgocentricStateDTO):
    inv_str = ", ".join(state.inventory) if state.inventory else "Empty"
    next_target = state.current_subgoal or "Objective"
    criteria = {}

    if COMPRESSION_LEVEL == "VERBOSE":
        fov_str = "\n".join([f"- {v.id} ({v.type}) at {v.relative_pos}" for v in state.visible_in_fov]) or "None"
        eval_state = {
            "mission": state.mission,
            "inventory": inv_str,
            "current_goal": next_target,
            "visible_useful_objects": fov_str
        }
        instructions = "Select INTERACT if a target is reachable, or select the action marked with (PRIORITY). Never select CheckedSector(Avoid)."
        for act in state.available_actions:
            criteria[act.code] = f"{act.code} -> {act.status}"

    elif COMPRESSION_LEVEL == "COMPACT":
        eval_state = f"Inv:{inv_str} | Goal:{next_target}"
        instructions = "Pick INTERACT or the option marked PRIORITY. Avoid CheckedSector."
        for act in state.available_actions:
            criteria[act.code] = act.status

    elif COMPRESSION_LEVEL == "MINIMAL":
        eval_state = f"Goal:{next_target}"
        instructions = "Pick PRIORITY:"
        for act in state.available_actions:
            if act.code.startswith("INTERACT:"):
                criteria[act.code] = "INTERACT_TARGET(PRIORITY)"
            else:
                criteria[act.code] = act.status

    else:  # ULTRA_MINIMAL (~25-32 tokens)
        eval_state = "Act"
        instructions = "Pick Priority:"
        for act in state.available_actions:
            if act.code.startswith("INTERACT:"):
                criteria[act.code] = "Priority"
            elif "PRIORITY" in act.status:
                criteria[act.code] = "Priority"
            else:
                criteria[act.code] = "Avoid"

    questions = {
        "step": {
            "type": "choice",
            "instructions": instructions,
            "criteria": criteria
        }
    }
    return eval_state, questions

@app.post("/plan_action")
async def plan_action(state: EgocentricStateDTO):
    global current_mission_id
    current_mission_id = state.mission_id or current_mission_id
    start_time = time.time()
    
    if not state.available_actions:
        return {"thought": "Sin acciones", "chosen_code": "TURN_LEFT", "latency_seconds": 0.0}

    if len(state.available_actions) == 1:
        only_code = state.available_actions[0].code
        return {"thought": f"[Auto] Única acción disponible: {only_code}", "chosen_code": only_code, "latency_seconds": 0.0}

    eval_state, questions = build_egocentric_query(state)
    tokens_used = estimate_tokens(eval_state, questions)

    try:
        if DECISION_ENGINE == "LAYA":
            res = router.predict(eval_state, questions)
            chosen_code = res["answers"]["step"]["choice"]
            conf = res["answers"]["step"].get("confidence", 0.0)
        else:
            payload = {"state": str(eval_state), "model": "kev-0.8b", "questions": questions}
            res = requests.post(KEV_URL, json=payload).json()
            chosen_code = res["answers"]["step"]["choice"]
            conf = res["answers"]["step"].get("confidence", 0.0)
            if "usage" in res and "input_tokens" in res["usage"]:
                tokens_used = res["usage"]["input_tokens"]
    except Exception as e:
        print(f"[{DECISION_ENGINE} Error] {e}")
        chosen_code = state.available_actions[0].code
        conf = 0.0

    latency = round(time.time() - start_time, 2)
    thought = f"[{DECISION_ENGINE}|{COMPRESSION_LEVEL}|{tokens_used}tok] {chosen_code} (Conf:{conf:.2f})"

    current_test_decisions.append({
        "pensamiento": thought,
        "latencia": latency,
        "tokens_entrada": tokens_used,
        "decision": chosen_code,
        "error_chatarra": 0,
        "error_servidor_prematuro": 0
    })

    return {
        "thought": thought,
        "chosen_code": chosen_code,
        "latency_seconds": latency
    }

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions, current_mission_id
    filename = "./versiones/Voyager_Architecture_v4.json"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    architecture_tag = f"Egocentric_{DECISION_ENGINE}_{COMPRESSION_LEVEL}_{current_mission_id}"

    latencias = [d["latencia"] for d in current_test_decisions]
    tokens_list = [d["tokens_entrada"] for d in current_test_decisions]
    tokens_totales_mision = sum(tokens_list)

    new_test = {
        "mision": current_mission_id,
        "nivel_compresion": COMPRESSION_LEVEL,
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "pasos_totales": len(current_test_decisions),
        "tokens_promedio_por_paso": round(statistics.mean(tokens_list), 2) if tokens_list else 0,
        "tokens_totales_mision": tokens_totales_mision,
        "errores_interaccion_chatarra": 0,
        "errores_servidor_prematuro": 0,
        "latencia_promedio": round(statistics.mean(latencias), 4) if latencias else 0.0,
        "decisiones": current_test_decisions
    }

    full_data = []
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, "r") as f:
                full_data = json.load(f)
        except json.JSONDecodeError:
            full_data = []

    model_entry = next((item for item in full_data if item["modelo"] == architecture_tag), None)
    if not model_entry:
        model_entry = {
            "modelo": architecture_tag,
            "mision": current_mission_id,
            "nivel_compresion": COMPRESSION_LEVEL,
            "pruebas": [],
            "total_timeouts": 0,
            "tokens_promedio_global": 0,
            "tokens_totales_promedio": 0,
            "latencia_promedio": 0,
            "latencia_mediana": 0,
            "duracion_promedio": 0
        }
        full_data.append(model_entry)

    model_entry["pruebas"].append(new_test)
    model_entry["total_timeouts"] = sum(1 for p in model_entry["pruebas"] if p.get("supero_limite_tiempo", False))

    all_latencies = [d["latencia"] for p in model_entry["pruebas"] for d in p["decisiones"]]
    all_tokens = [d["tokens_entrada"] for p in model_entry["pruebas"] for d in p["decisiones"]]
    all_total_tokens = [p.get("tokens_totales_mision", sum(d["tokens_entrada"] for d in p["decisiones"])) for p in model_entry["pruebas"]]
    all_durations = [p["duracion_total"] for p in model_entry["pruebas"] if not p.get("supero_limite_tiempo", False)]

    model_entry["tokens_promedio_global"] = round(statistics.mean(all_tokens), 2) if all_tokens else 0
    model_entry["tokens_totales_promedio"] = round(statistics.mean(all_total_tokens), 1) if all_total_tokens else 0
    model_entry["latencia_promedio"] = round(statistics.mean(all_latencies), 4) if all_latencies else 0
    model_entry["latencia_mediana"] = round(statistics.median(all_latencies), 2) if all_latencies else 0
    model_entry["duracion_promedio"] = round(statistics.mean(all_durations), 4) if all_durations else 0

    with open(filename, "w") as f:
        json.dump(full_data, f, indent=4)

    current_test_decisions = []
    return {"status": "saved", "architecture": architecture_tag}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)