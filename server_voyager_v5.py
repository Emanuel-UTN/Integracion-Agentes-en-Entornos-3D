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

# --- CONFIGURACIÓN V5: AUTONOMÍA ESTRATÉGICA ---
DECISION_ENGINE = "KEV"               
MODEL_THINKER = "qwen2.5:7b"          
COMPRESSION_LEVEL = "MINIMAL"         # "VERBOSE", "COMPACT", "MINIMAL", "ULTRA_MINIMAL"
CONFIDENCE_THRESHOLD = 0.15

KEV_URL = "http://127.0.0.1:8009/v1/systemone"
OLLAMA_URL = "http://localhost:11434/api/generate"

ARCHITECTURE_TAG = f"AutoV5_{DECISION_ENGINE}_{COMPRESSION_LEVEL}+LLM"

router = None
current_test_decisions = []
current_mission_id = "Mision_Autonoma"

# --- MEMORIA CACHÉ DE EVENTOS ---
active_subgoal = ""
last_inventory = []
last_remembered_ids = set()
last_failed_target = "NONE"

class VisibleEntityDTO(BaseModel):
    id: str
    type: str
    description: str

class KnownEntitySummaryDTO(BaseModel):
    id: str
    type: str
    description: str
    is_reachable_now: bool

class ActionOptionDTO(BaseModel):
    code: str
    status: str 

class EgocentricStateDTO(BaseModel):
    mission_id: str = "Mision_Autonoma"
    mission: str = ""
    final_goal: str = ""
    inventory: List[str] = []
    newly_spotted: List[VisibleEntityDTO] = []
    remembered_entities: List[KnownEntitySummaryDTO] = []
    available_actions: List[ActionOptionDTO] = []

class BenchmarkEnd(BaseModel):
    duracion_total: float
    timed_out: bool = False

def estimate_tokens(state_str: str, questions_obj: dict) -> int:
    raw = f"{state_str} {json.dumps(questions_obj, ensure_ascii=False)}"
    opts_count = sum(len(q.get("criteria", {})) for q in questions_obj.values())
    return int(len(raw) / 3.8) + 4 + (opts_count * 2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global router
    print(f"\n[V5] Iniciando Arquitectura Genérica. Nivel: {COMPRESSION_LEVEL}")
    try:
        requests.post(OLLAMA_URL, json={"model": MODEL_THINKER, "prompt": "wake up", "keep_alive": -1})
        print(f"[OK] Lóbulo Estratégico ({MODEL_THINKER}) anclado en VRAM.")
    except Exception as e:
        print(f"[Aviso] No se pudo conectar a Ollama: {e}")
        
    if DECISION_ENGINE == "LAYA":
        router = Router(preload=True, device="cpu")
        print(f"[OK] Lóbulo Motor (LAYA) cargado en CPU.")
    else:
        print(f"[OK] Lóbulo Motor (KEV) configurado por API.")
    yield

app = FastAPI(title="Voyager V5 - Generic Autonomy", lifespan=lifespan)

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, active_subgoal, last_inventory, last_remembered_ids, last_failed_target
    current_test_decisions = []
    active_subgoal = ""
    last_inventory = []
    last_remembered_ids = set()
    last_failed_target = "NONE"
    return {"status": "cleared"}

def run_inference(state_str: str, questions: dict):
    tokens_used = estimate_tokens(state_str, questions)
    if DECISION_ENGINE == "LAYA":
        res = router.predict(state_str, questions)
        return res, tokens_used
    else:
        payload = {"state": state_str, "model": "kev-0.8b", "questions": questions}
        res = requests.post(KEV_URL, json=payload).json()
        if "usage" in res and "input_tokens" in res["usage"]:
            tokens_used = res["usage"]["input_tokens"]
        return res, tokens_used

# =====================================================================
# Q1: CLASIFICACIÓN VISUAL A DISTANCIA (KEV/LAYA)
# =====================================================================
@app.post("/evaluate_entities")
async def evaluate_entities(state: EgocentricStateDTO):
    global current_mission_id
    current_mission_id = state.mission_id or "Mision_Autonoma"
    useful_ids = []
    useless_ids = []

    for ent in state.newly_spotted:
        start_t = time.time()

        if COMPRESSION_LEVEL == "VERBOSE":
            eval_state = f"Mission: {state.mission}\nObject: {ent.type} ({ent.description})"
            instructions = "Evaluate this object based on the mission. Is it useful or useless scrap?"
            criteria = {
                "USEFUL": "Yes, it is a required objective, tool, key, door, or terminal.",
                "USELESS": "No, it is rusted metal, broken robot part, debris, or scrap."
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
        else:  # ULTRA_MINIMAL
            eval_state = f"M:{state.mission}|O:{ent.type}:{ent.description}"
            instructions = "Useful?"
            criteria = {
                "USEFUL": "Useful target/tool",
                "USELESS": "Useless scrap"
            }

        q1 = {"relevance": {"type": "choice", "instructions": instructions, "criteria": criteria}}

        try:
            res, tok = run_inference(eval_state, q1)
            choice = res["answers"]["relevance"]["choice"]
        except Exception:
            choice, tok = ("USELESS" if "Scrap" in ent.type else "USEFUL"), 0

        lat = round(time.time() - start_t, 2)
        if choice == "USEFUL": useful_ids.append(ent.id)
        else: useless_ids.append(ent.id)

        current_test_decisions.append({
            "pensamiento": f"[Q1-Vision|{tok}tok] {ent.type} -> {choice}",
            "latencia": lat, "tokens_entrada": tok, "decision": f"SCAN:{ent.id}={choice}"
        })

    return {"useful_ids": useful_ids, "useless_ids": useless_ids}

# =====================================================================
# Q2: ESTRATEGIA (LLM) + MOTOR FÍSICO (KEV/LAYA)
# =====================================================================
def get_strategic_subgoal(state: EgocentricStateDTO) -> str:
    """
    LLM: Construye su propia estrategia basada en el entorno.
    Se le exige estrictez sintáctica para no marear a KEV.
    """
    inv_str = ", ".join(state.inventory) if state.inventory else "Empty"
    known_str = ", ".join([f"{e.type} ({e.description})" for e in state.remembered_entities]) if state.remembered_entities else "None"
    
    prompt = f"""
    MISSION: {state.mission}
    YOUR INVENTORY: [{inv_str}]
    USEFUL OBJECTS KNOWN: [{known_str}]

    Based on your inventory and the known objects, what is your immediate next logical target entity?
    Respond ONLY with the exact noun/name of the entity type. Do not use verbs, do not use full sentences. If you are stuck or need to find unknown areas, respond with 'Explore'.
    """
    try:
        res = requests.post(OLLAMA_URL, json={
            "model": MODEL_THINKER, "prompt": prompt.strip(), "stream": False, "temperature": 0.0
        }).json()
        
        # Obtenemos la respuesta cruda del LLM (ej: "Keycard") sin hardcodear listas.
        raw_response = res["response"].strip().replace('"', '').replace('\n', '')
        # Tomamos solo la primera palabra por si el LLM decide escribir una oración corta
        clean_target = raw_response.split()[0] if raw_response else "Explore"
        return clean_target
        
    except Exception as e:
        print(f"[Ollama Error] {e}")
        return "Explore"

def build_q2_query(state: EgocentricStateDTO, active_goal: str):
    """Construye el estado y opciones adaptados a la compresión."""
    inv_str = ", ".join(state.inventory) if state.inventory else "Empty"
    criteria = {}

    for act in state.available_actions:
        if act.code.startswith("INTERACT:"):
            # Enfatizamos a KEV que esta es la interacción final
            target_name = act.status.split()[-1] if act.status else act.code.split(":")[1]
            if COMPRESSION_LEVEL == "VERBOSE":
                criteria[act.code] = f"Execute Interaction with {target_name}."
            elif COMPRESSION_LEVEL == "COMPACT":
                criteria[act.code] = f"Interact with {target_name}"
            elif COMPRESSION_LEVEL == "MINIMAL":
                criteria[act.code] = f"INTERACT with {target_name}"
            else:
                criteria[act.code] = f"Use {target_name}"
        else:
            # Mantenemos las rutas neutrales generadas por Unity
            if COMPRESSION_LEVEL == "ULTRA_MINIMAL":
                compact_route = act.status.replace("Leads to: ", "->").replace(" steps", "s").replace("Nothing useful this way", "Empty")
                criteria[act.code] = compact_route
            else:
                criteria[act.code] = act.status

    if COMPRESSION_LEVEL == "VERBOSE":
        eval_state = f"Target Goal: {active_goal}\nInventory: [{inv_str}]"
        instructions = "Select the movement that 'Leads to' your Target Goal, or 'Interact' with it if you are ready. If your goal is Explore, choose Unexplored areas."
    elif COMPRESSION_LEVEL == "COMPACT":
        eval_state = f"Target:{active_goal} | Inv:[{inv_str}]"
        instructions = "Select the action that leads to or interacts with your Target."
    elif COMPRESSION_LEVEL == "MINIMAL":
        eval_state = f"Goal:{active_goal}"
        instructions = "Match action to your Goal:"
    else:  # ULTRA_MINIMAL
        eval_state = f"G:{active_goal}"
        instructions = "Act:"

    questions = {"step": {"type": "choice", "instructions": instructions, "criteria": criteria}}
    return eval_state, questions

@app.post("/plan_action")
async def plan_action(state: EgocentricStateDTO):
    global active_subgoal, last_inventory, last_remembered_ids, last_failed_target
    start_time = time.time()
    
    if not state.available_actions:
        return {"thought": "No actions", "chosen_code": "TURN_AROUND", "latency_seconds": 0.0}
    if len(state.available_actions) == 1:
        return {"thought": "Auto", "chosen_code": state.available_actions[0].code, "latency_seconds": 0.0}

    interaction_failed = False
    for act in state.available_actions:
        if act.code.startswith("INTERACT:") and "(FAILED RECENTLY)" in act.status:
            if last_failed_target != act.code:
                interaction_failed = True
                last_failed_target = act.code
                break

    current_remembered_ids = set(e.id for e in state.remembered_entities)

    # --- TRIGGERS DEL LLM ---
    trigger_llm = False
    motivo = ""

    if not active_subgoal:
        trigger_llm, motivo = True, "Inicio de Misión"
    elif state.inventory != last_inventory:
        trigger_llm, motivo = True, f"Inventario Cambió: {state.inventory}"
    elif current_remembered_ids != last_remembered_ids:
        trigger_llm, motivo = True, "Cambio en objetos del entorno"
    elif interaction_failed:
        trigger_llm, motivo = True, "Fallo Físico de Interacción"

    if trigger_llm:
        print(f"\n<color=cyan>[LLM Trigger] {MODEL_THINKER} | Motivo: {motivo}</color>")
        t0 = time.time()
        active_subgoal = get_strategic_subgoal(state)
        print(f"<color=cyan>[LLM] Nueva Meta: '{active_subgoal}' ({(time.time()-t0):.2f}s)</color>")
        last_inventory = list(state.inventory)
        last_remembered_ids = current_remembered_ids

    eval_state, q2 = build_q2_query(state, active_subgoal)

    try:
        res, tok = run_inference(eval_state, q2)
        choice = res["answers"]["step"]["choice"]
        conf = res["answers"]["step"].get("confidence", 0.0)
    except Exception as e:
        print(f"[Q2 Error] {e}")
        choice, conf, tok = state.available_actions[0].code, 0.0, 0

    if conf < CONFIDENCE_THRESHOLD and choice != "TURN_LEFT":
        choice = "TURN_LEFT" 
        thought = f"[{DECISION_ENGINE}|{COMPRESSION_LEVEL}|{tok}t] Duda ({conf:.2f}). Forzado rotar."
    else:
        thought = f"[{DECISION_ENGINE}|{COMPRESSION_LEVEL}|{tok}t] (Meta: {active_subgoal}) -> {choice} (Conf: {conf:.2f})"

    lat = round(time.time() - start_time, 2)
    current_test_decisions.append({
        "pensamiento": thought, "latencia": lat, "tokens_entrada": tok, "decision": choice
    })

    return {"thought": thought, "chosen_code": choice, "latency_seconds": lat}

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions, current_mission_id, active_subgoal, last_inventory, last_remembered_ids, last_failed_target
    filename = "./versiones/Voyager_Architecture_v5.json"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    latencias = [d["latencia"] for d in current_test_decisions]
    tokens_list = [d.get("tokens_entrada", 0) for d in current_test_decisions]

    new_test = {
        "mision": current_mission_id,
        "nivel_compresion": COMPRESSION_LEVEL,
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "pasos_totales": len(current_test_decisions),
        "tokens_promedio_por_paso": round(statistics.mean(tokens_list), 2) if tokens_list else 0,
        "latencia_promedio": round(statistics.mean(latencias), 4) if latencias else 0.0,
        "decisiones": current_test_decisions
    }

    full_data = []
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, "r") as f: full_data = json.load(f)
        except Exception: pass

    model_entry = next((item for item in full_data if item["modelo"] == ARCHITECTURE_TAG), None)
    if not model_entry:
        model_entry = {"modelo": ARCHITECTURE_TAG, "pruebas": [], "total_timeouts": 0}
        full_data.append(model_entry)

    model_entry["pruebas"].append(new_test)
    model_entry["total_timeouts"] = sum(1 for p in model_entry["pruebas"] if p.get("supero_limite_tiempo", False))

    with open(filename, "w") as f:
        json.dump(full_data, f, indent=4)

    current_test_decisions = []
    active_subgoal = ""
    last_inventory = []
    last_remembered_ids = set()
    last_failed_target = "NONE"
    return {"status": "saved", "architecture": ARCHITECTURE_TAG}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)