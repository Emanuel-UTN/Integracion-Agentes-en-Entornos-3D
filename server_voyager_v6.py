import time
import requests
import json
import os
import re
import statistics
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Dict

# --- CONFIGURACIÓN V6.3: FAST ReAct + ATOMIC REASONING PIPELINE ---
DECISION_ENGINE = "KEV"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

INTENT_CONFIDENCE_TH = 0.15
ARCHITECTURE_TAG = f"Agnostic_ReAct_V6_{DECISION_ENGINE}"

current_test_decisions = []
current_mission_id = "Mision_V6_Agnostica"

# --- MEMORIA COGNITIVA PERSISTENTE ---
active_target_id = "EXPLORE"
active_target_type = "EXPLORE"

known_useful_ids = set()
known_useless_ids = set()
completed_entity_ids = set()

# Caché semántico por tipo: { "Keycard": "PICKUP_ITEM", "MainServer": "ENVIRONMENT_DEVICE", ... }
entity_type_semantics_cache: Dict[str, str] = {}

entity_feedback_memory: Dict[str, str] = {}
recent_observations: List[str] = []

last_inventory: List[str] = []
last_remembered_ids = set()
last_action_taken = "NONE"

class EntityHudDTO(BaseModel):
    id: str
    type: str
    description: str
    sector: str = "NONE"
    steps: int = 99

class ActionOptionDTO(BaseModel):
    code: str
    status: str

class UnifiedHudReActStateDTO(BaseModel):
    mission_id: str = "Mision_V6_Agnostica"
    mission: str = ""
    inventory: List[str] = []
    last_observation: str = ""
    crosshair_entity_id: str = "NONE"
    newly_spotted: List[EntityHudDTO] = []
    remembered_entities: List[EntityHudDTO] = []
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
    print(f"\n[Agnostic ReAct V6.3] Motor: {DECISION_ENGINE} | Modo: Descomposición Atómica Genérica.")
    yield

app = FastAPI(title="Voyager V6.3 - Agnostic ReAct Planner", lifespan=lifespan)

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, active_target_id, active_target_type
    global known_useful_ids, known_useless_ids, completed_entity_ids
    global entity_type_semantics_cache, entity_feedback_memory, recent_observations
    global last_inventory, last_remembered_ids, last_action_taken

    current_test_decisions = []
    active_target_id = "EXPLORE"
    active_target_type = "EXPLORE"
    known_useful_ids.clear()
    known_useless_ids.clear()
    completed_entity_ids.clear()
    entity_type_semantics_cache.clear()
    entity_feedback_memory.clear()
    recent_observations.clear()
    last_inventory = []
    last_remembered_ids.clear()
    last_action_taken = "NONE"
    return {"status": "cleared"}

def run_inference(state_dict: dict, questions: dict) -> tuple[dict, int]:
    state_str = json.dumps(state_dict, ensure_ascii=False)
    tokens_used = estimate_tokens(state_str, questions)
    payload = {"state": state_str, "model": "kev-0.8b", "questions": questions}
    res = requests.post(KEV_URL, json=payload).json()
    if "usage" in res and "input_tokens" in res["usage"]:
        tokens_used = res["usage"]["input_tokens"]
    return res.get("answers", {}), tokens_used

# =====================================================================
# 1. EVALUADOR DE EVENTOS ReAct (MEMORIA DE FRUSTRACIÓN ESTRICTA)
# =====================================================================
def evaluate_react_feedback(state: UnifiedHudReActStateDTO) -> tuple[str, bool]:
    global last_inventory, last_remembered_ids, entity_feedback_memory, completed_entity_ids

    current_ids = set(e.id for e in state.remembered_entities if e.id not in known_useless_ids)
    new_items = [item for item in state.inventory if item not in last_inventory]
    consumed_ids = last_remembered_ids - current_ids

    unity_obs = state.last_observation.strip() if state.last_observation else ""
    trigger_replan = False

    if last_action_taken == "NONE":
        obs = unity_obs or "OBSERVATION: Mission started. Inventory is empty."
        trigger_replan = True

    elif last_action_taken.startswith("INTERACT:"):
        target_id = last_action_taken.split(":")[1]
        trigger_replan = True

        if "SUCCESS" in unity_obs or new_items:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} SUCCEEDED! Inventory updated."
            completed_entity_ids.add(target_id)
            # Solo reseteamos la memoria de fallos si entró un nuevo ítem físico
            if len(new_items) > 0:
                entity_feedback_memory.clear()
        elif target_id in consumed_ids or len(consumed_ids) > 0:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} SUCCEEDED! Objective completed."
            completed_entity_ids.add(target_id)
            if len(new_items) > 0:
                entity_feedback_memory.clear()
        else:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} FAILED! Missing required condition."
            entity_feedback_memory[target_id] = "FAILED"

    else:
        obs = unity_obs or f"OBSERVATION: Executed {last_action_taken}."
        if len(new_items) > 0:
            entity_feedback_memory.clear()
            trigger_replan = True
        elif len(consumed_ids) > 0:
            trigger_replan = True

    last_inventory = list(state.inventory)
    last_remembered_ids = current_ids
    return obs, trigger_replan

# =====================================================================
# 2. Q1: PERCEPCIÓN SEMÁNTICA MEDIANTE TAXONOMÍA FUNCIONAL UNIVERSAL
# =====================================================================
def classify_spotted_entities(state: UnifiedHudReActStateDTO) -> tuple[List[str], List[str], int, bool]:
    new_useful, new_useless = [], []
    total_tok = 0
    found_new_useful = False

    for spotted in state.newly_spotted:
        if spotted.type in entity_type_semantics_cache:
            role = entity_type_semantics_cache[spotted.type]
        else:
            q1_state = {
                "entity": spotted.type,
                "description": spotted.description
            }
            q1 = {
                "role": {
                    "type": "choice",
                    "instructions": f"Analyze '{spotted.type}' with description '{spotted.description}'. What functional role does this entity represent?",
                    "criteria": {
                        "PICKUP_ITEM": "A loose portable item lying on the ground (such as a keycard, cartridge, data drive, or tool) that can be picked up into inventory.",
                        "ENVIRONMENT_DEVICE": "A fixed stationary structure, door, barrier, machine, terminal, or console in the room that can be operated or unlocked.",
                        "USELESS_SCRAP": "Inert scrap metal, rubble, junk, or debris that has no practical function."
                    }
                }
            }

            try:
                ans, tok = run_inference(q1_state, q1)
                role = ans.get("role", {}).get("choice", "PICKUP_ITEM")
                total_tok += tok
            except Exception:
                if "Scrap" in spotted.type:
                    role = "USELESS_SCRAP"
                elif "Door" in spotted.type or "Server" in spotted.type:
                    role = "ENVIRONMENT_DEVICE"
                else:
                    role = "PICKUP_ITEM"

            entity_type_semantics_cache[spotted.type] = role
            print(f"[Q1 Percepción] {spotted.id} ({spotted.type}) clasificado como: {role}")

        if role != "USELESS_SCRAP":
            known_useful_ids.add(spotted.id)
            new_useful.append(spotted.id)
            found_new_useful = True
        else:
            known_useless_ids.add(spotted.id)
            new_useless.append(spotted.id)

    return new_useful, new_useless, total_tok, found_new_useful

# =====================================================================
# 3. FILTRO DE ACCIONABILIDAD FÍSICA (EVALUACIÓN CON CONTEXTO COMPLETO)
# =====================================================================
def get_actionable_entities_agnostic(entities: List[EntityHudDTO], inventory: List[str]) -> tuple[List[EntityHudDTO], int]:
    actionable = []
    total_tokens = 0
    inv_str = ", ".join(inventory) if inventory else "Empty (No items)"

    candidates = [
        e for e in entities
        if e.id in known_useful_ids 
        and e.id not in entity_feedback_memory 
        and e.id not in completed_entity_ids
    ]

    devices_to_verify = []
    for e in candidates:
        role = entity_type_semantics_cache.get(e.type, "PICKUP_ITEM")
        
        # 1. Los ítems del suelo se recogen directamente con las manos
        if role == "PICKUP_ITEM":
            actionable.append(e)
        elif role == "ENVIRONMENT_DEVICE":
            devices_to_verify.append(e)

    if not devices_to_verify:
        return actionable, 0

    # 2. Si el inventario está vacío, ningún dispositivo con requisitos puede operarse
    if not inventory:
        return actionable, 0

    # 3. Pasamos tanto el inventario como las descripciones completas en eval_state
    eval_state = {
        "held_inventory": inv_str,
        "target_devices": "\n".join([f"- {d.id} ({d.type}): {d.description}" for d in devices_to_verify])
    }

    questions = {}
    for d in devices_to_verify:
        questions[f"can_use_{d.id}"] = {
            "type": "choice",
            "instructions": (
                f"Compare held_inventory [{inv_str}] with the requirement of '{d.type}' ({d.description}). "
                f"Does the agent currently hold the item required to use '{d.type}'?"
            ),
            "criteria": {
                "READY": f"YES. The required item described for {d.type} is present in held_inventory [{inv_str}].",
                "NOT_READY": f"NO. The required item described for {d.type} is missing from held_inventory [{inv_str}]."
            }
        }

    try:
        ans, tok = run_inference(eval_state, questions)
        total_tokens += tok
        for d in devices_to_verify:
            choice = ans.get(f"can_use_{d.id}", {}).get("choice", "NOT_READY")
            if choice == "READY":
                actionable.append(d)
    except Exception as err:
        print(f"[Device Verification Error] {err}")

    return actionable, total_tokens

# =====================================================================
# 4. Q2 ESTRATÉGICO: SELECCIÓN DE META ENTRE ACCIONES VIABLES
# =====================================================================
def update_strategic_goal_react(state: UnifiedHudReActStateDTO, actionable_entities: List[EntityHudDTO]) -> tuple[str, str, float, int]:
    if not actionable_entities:
        return "EXPLORE", "EXPLORE", 1.0, 0

    # Si hay una única entidad viable, la fija de inmediato (100% certeza, 0 tokens)
    if len(actionable_entities) == 1:
        target = actionable_entities[0]
        return target.id, target.type, 1.0, 0

    inv_str = ", ".join(state.inventory) if state.inventory else "Empty"
    eval_state_q2 = {
        "mission": state.mission,
        "current_inventory": inv_str,
        "ready_targets": "\n".join([f"- {e.id} ({e.type}): {e.description}" for e in actionable_entities])
    }

    criteria = {"EXPLORE": "Explore to find new paths or items."}
    for e in actionable_entities:
        criteria[e.id] = f"Interact with {e.type} ({e.id}): {e.description}"

    q2 = {
        "decision": {
            "type": "choice",
            "instructions": f"All listed ready_targets are accessible right now with your current_inventory [{inv_str}]. Which entity do you target to progress in the mission?",
            "criteria": criteria
        }
    }

    try:
        ans, tok = run_inference(eval_state_q2, q2)
        choice = ans.get("decision", {}).get("choice", actionable_entities[0].id)
        conf = ans.get("decision", {}).get("confidence", 0.0)

        if choice == "EXPLORE" or conf < INTENT_CONFIDENCE_TH:
            return "EXPLORE", "EXPLORE", conf, tok

        target_ent = next((e for e in actionable_entities if e.id == choice), actionable_entities[0])
        return target_ent.id, target_ent.type, conf, tok
    except Exception as e:
        print(f"[Q2 Error] {e}")
        return actionable_entities[0].id, actionable_entities[0].type, 1.0, 0

# =====================================================================
# 5. PODA DE SENSORES PARA Q3 (NAVEGACIÓN MOTORA EGOCÉNTRICA)
# =====================================================================
def build_pruned_q3_criteria(motor_actions: List[ActionOptionDTO], target_type: str) -> dict:
    criteria = {}
    if target_type != "EXPLORE":
        pattern = re.compile(rf"{re.escape(target_type)}\s*\([^)]+\)")
        for act in motor_actions:
            match = pattern.search(act.status)
            if match:
                criteria[act.code] = f"Leads directly to target {match.group(0)}"
            else:
                criteria[act.code] = "Does not lead to current target"
    else:
        for act in motor_actions:
            if "Unexplored" in act.status or "Scan" in act.status:
                parts = [p.strip() for p in act.status.replace("Leads to:", "").split(",") if "Unexplored" in p or "Scan" in p]
                criteria[act.code] = "Leads to: " + ", ".join(parts)
            else:
                criteria[act.code] = "Already explored direction"
    return criteria

# =====================================================================
# 6. ENDPOINT PRINCIPAL DE PLANIFICACIÓN
# =====================================================================
@app.post("/plan_action")
async def plan_action(state: UnifiedHudReActStateDTO):
    global current_mission_id, active_target_id, active_target_type, last_action_taken
    global recent_observations
    start_time = time.time()
    current_mission_id = state.mission_id or "Mision_V6_Agnostica"

    # 1. Q1: Percepción semántica
    new_useful, new_useless, q1_tok, spotted_new_useful = classify_spotted_entities(state)

    # 2. ReAct: Evaluación de la acción anterior
    latest_obs, trigger_replan = evaluate_react_feedback(state)
    recent_observations.append(latest_obs)
    if len(recent_observations) > 3:
        recent_observations.pop(0)

    # Entidades en mundo activas que no se hayan completado
    active_ids_in_world = {
        e.id for e in state.remembered_entities
        if e.id not in known_useless_ids and e.id not in completed_entity_ids
    }

    # 3. Disparador estricto de eventos
    should_run_q2 = (
        trigger_replan
        or spotted_new_useful
        or (active_target_id != "EXPLORE" and active_target_id not in active_ids_in_world)
        or (active_target_id in entity_feedback_memory)
    )

    q2_tok = 0
    q2_conf = 1.0
    req_tok = 0

    if should_run_q2:
        actionable_entities, req_tok = get_actionable_entities_agnostic(state.remembered_entities, state.inventory)
        active_target_id, active_target_type, q2_conf, q2_tok = update_strategic_goal_react(state, actionable_entities)
        print(f"<color=cyan>[ReAct Meta] Obs: '{latest_obs}' -> Nueva Meta: {active_target_id} ({active_target_type}) [Conf: {q2_conf:.2f}]</color>")

    # 4. Disparo directo en mira si ya alcanzamos físicamente la meta
    if active_target_id != "EXPLORE":
        interact_code = f"INTERACT:{active_target_id}"
        if any(a.code == interact_code for a in state.available_actions):
            lat = round(time.time() - start_time, 2)
            total_tok = q1_tok + req_tok + q2_tok
            last_action_taken = interact_code
            thought = f"[AgnosticReAct|{total_tok}t] Target Reached: {active_target_id} => {interact_code}"
            current_test_decisions.append({
                "observacion": latest_obs,
                "meta_activa": f"{active_target_id}({active_target_type})",
                "pensamiento": thought,
                "latencia": lat,
                "tokens_entrada": total_tok,
                "decision": interact_code
            })
            return {
                "thought": thought,
                "chosen_code": interact_code,
                "latency_seconds": lat,
                "useful_ids": new_useful,
                "useless_ids": new_useless
            }

    # 5. Navegación motora egocéntrica (Q3)
    motor_actions = [
        a for a in state.available_actions
        if not a.code.startswith("INTERACT:") and "Blocked" not in a.status
    ]

    if not motor_actions:
        last_action_taken = "TURN_AROUND"
        return {
            "thought": "Fallback TURN_AROUND",
            "chosen_code": "TURN_AROUND",
            "latency_seconds": round(time.time() - start_time, 2),
            "useful_ids": new_useful,
            "useless_ids": new_useless
        }

    if len(motor_actions) == 1:
        chosen_code = motor_actions[0].code
        lat = round(time.time() - start_time, 2)
        total_tok = q1_tok + req_tok + q2_tok
        last_action_taken = chosen_code
        thought = f"[AgnosticReAct-Auto|{total_tok}t] Goal: {active_target_id} => {chosen_code}"
        current_test_decisions.append({
            "observacion": latest_obs,
            "meta_activa": f"{active_target_id}({active_target_type})",
            "pensamiento": thought,
            "latencia": lat,
            "tokens_entrada": total_tok,
            "decision": chosen_code
        })
        return {
            "thought": thought,
            "chosen_code": chosen_code,
            "latency_seconds": lat,
            "useful_ids": new_useful,
            "useless_ids": new_useless
        }

    pruned_criteria = build_pruned_q3_criteria(motor_actions, active_target_type)
    eval_state_q3 = {"current_target": active_target_type}
    q3 = {
        "step": {
            "type": "choice",
            "instructions": (
                f"Select the action that leads directly to target {active_target_type}."
                if active_target_type != "EXPLORE"
                else "Select the action that leads to an Unexplored area or Scan."
            ),
            "criteria": pruned_criteria
        }
    }

    q3_tok = 0
    q3_conf = 0.0
    try:
        ans_q3, q3_tok = run_inference(eval_state_q3, q3)
        chosen_code = ans_q3.get("step", {}).get("choice", motor_actions[0].code)
        q3_conf = ans_q3.get("step", {}).get("confidence", 0.0)
    except Exception as e:
        print(f"[Q3 Error] {e}")
        chosen_code = motor_actions[0].code

    if active_target_type != "EXPLORE":
        direct_matches = [code for code, desc in pruned_criteria.items() if desc.startswith("Leads directly to target")]
        if len(direct_matches) == 1 and chosen_code != direct_matches[0]:
            chosen_code = direct_matches[0]

    total_tok = q1_tok + req_tok + q2_tok + q3_tok
    lat = round(time.time() - start_time, 2)
    last_action_taken = chosen_code

    thought = (
        f"[AgnosticReAct|{total_tok}t] Obs: '{latest_obs}' | "
        f"Goal: {active_target_id}({active_target_type}) -> Act: {chosen_code} (Conf: {q3_conf:.2f})"
    )

    current_test_decisions.append({
        "observacion": latest_obs,
        "meta_activa": f"{active_target_id}({active_target_type})",
        "pensamiento": thought,
        "latencia": lat,
        "tokens_entrada": total_tok,
        "decision": chosen_code
    })

    return {
        "thought": thought,
        "chosen_code": chosen_code,
        "latency_seconds": lat,
        "useful_ids": new_useful,
        "useless_ids": new_useless
    }

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions, current_mission_id
    filename = "./versiones/Voyager_Architecture_v6_Agnostica.json"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    latencias = [d["latencia"] for d in current_test_decisions]
    tokens_list = [d.get("tokens_entrada", 0) for d in current_test_decisions]

    new_test = {
        "mision": current_mission_id,
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
            with open(filename, "r") as f:
                full_data = json.load(f)
        except Exception:
            pass

    model_entry = next((item for item in full_data if item["modelo"] == ARCHITECTURE_TAG), None)
    if not model_entry:
        model_entry = {"modelo": ARCHITECTURE_TAG, "pruebas": [], "total_timeouts": 0}
        full_data.append(model_entry)

    model_entry["pruebas"].append(new_test)
    model_entry["total_timeouts"] = sum(1 for p in model_entry["pruebas"] if p.get("supero_limite_tiempo", False))

    with open(filename, "w") as f:
        json.dump(full_data, f, indent=4)

    await start_benchmark()
    return {"status": "saved", "architecture": ARCHITECTURE_TAG}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)