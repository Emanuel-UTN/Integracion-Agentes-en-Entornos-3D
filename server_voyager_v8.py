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

# --- CONFIGURACIÓN V8: ATOMIC REQUIREMENT DECOMPOSITION ---
DECISION_ENGINE = "KEV"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

INTENT_CONFIDENCE_TH = 0.15
ARCHITECTURE_TAG = f"Agnostic_ReAct_V8_{DECISION_ENGINE}"

current_test_decisions = []
current_mission_id = "Mision_V8_Agnostica"

# --- MEMORIA COGNITIVA PERSISTENTE ---
active_target_id = "EXPLORE"
active_target_type = "EXPLORE"

known_useful_ids = set()
known_useless_ids = set()
completed_entity_ids = set()

# Registra estados completados en el entorno (ej. ítems guardados en slots o switches activados)
completed_states_memory: List[str] = []

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
    mission_id: str = "Mision_V8_Agnostica"
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
    print(f"\n[Agnostic ReAct V8] Motor: {DECISION_ENGINE} | Modo: Descomposición Atómica de Cláusulas.")
    yield

app = FastAPI(title="Voyager V8 - Atomic Conjunctive ReAct Planner", lifespan=lifespan)

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, active_target_id, active_target_type
    global known_useful_ids, known_useless_ids, completed_entity_ids, completed_states_memory
    global entity_type_semantics_cache, entity_feedback_memory, recent_observations
    global last_inventory, last_remembered_ids, last_action_taken

    current_test_decisions = []
    active_target_id = "EXPLORE"
    active_target_type = "EXPLORE"
    known_useful_ids.clear()
    known_useless_ids.clear()
    completed_entity_ids.clear()
    completed_states_memory.clear()
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
# 1. EVALUADOR DE EVENTOS ReAct (REGISTRA ÍTEMS GUARDADOS Y ACTIVACIONES)
# =====================================================================
def evaluate_react_feedback(state: UnifiedHudReActStateDTO) -> tuple[str, bool]:
    global last_inventory, last_remembered_ids, entity_feedback_memory, completed_entity_ids, completed_states_memory

    current_ids = set(e.id for e in state.remembered_entities if e.id not in known_useless_ids)
    new_items = [item for item in state.inventory if item not in last_inventory]
    deposited_items = [item for item in last_inventory if item not in state.inventory]
    consumed_ids = last_remembered_ids - current_ids

    unity_obs = state.last_observation.strip() if state.last_observation else ""
    trigger_replan = False

    if last_action_taken == "NONE":
        obs = unity_obs or "OBSERVATION: Mission started. Inventory is empty."
        trigger_replan = True

    elif last_action_taken.startswith("INTERACT:"):
        target_id = last_action_taken.split(":")[1]
        trigger_replan = True

        if "SUCCESS" in unity_obs or len(new_items) > 0 or len(deposited_items) > 0 or target_id in consumed_ids:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} SUCCEEDED!"
            completed_entity_ids.add(target_id)
            entity_feedback_memory.clear()

            # Si se depositaron ítems en un slot/contenedor, guardamos constancia explícita
            for dep in deposited_items:
                entry = f"{dep} stored in {target_id}"
                if entry not in completed_states_memory:
                    completed_states_memory.append(entry)

            # Si se activó un switch/dispositivo sin entregar un ítem nuevo al inventario
            if not new_items:
                act_entry = f"{target_id} activated and stored"
                if act_entry not in completed_states_memory:
                    completed_states_memory.append(act_entry)
        else:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} FAILED! Missing required condition."
            entity_feedback_memory[target_id] = "FAILED"

    else:
        obs = unity_obs or f"OBSERVATION: Executed {last_action_taken}."
        if len(new_items) > 0 or len(deposited_items) > 0 or len(consumed_ids) > 0:
            entity_feedback_memory.clear()
            trigger_replan = True

    last_inventory = list(state.inventory)
    last_remembered_ids = current_ids
    return obs, trigger_replan

# =====================================================================
# 2. Q1: PERCEPCIÓN SEMÁNTICA (TAXONOMÍA FUNCIONAL V6)
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
                        "PICKUP_ITEM": "A loose portable item lying on the ground (such as a keycard, key, cartridge, data drive, battery, fuse, circuit, or tool) that can be picked up into inventory.",
                        "ENVIRONMENT_DEVICE": "A fixed stationary structure, door, barrier, slot, chest, vault, switch, valve, machine, terminal, or console in the room that can be operated or unlocked.",
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
                elif any(k in spotted.type for k in ["Door", "Server", "Slot", "Switch", "Console", "Vault", "Chest", "Reactor", "PC"]):
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
# 3. DESCOMPOSICIÓN ATÓMICA DE REQUISITOS CONJUNTIVOS (A & B & C)
# =====================================================================
def extract_atomic_requirements(description: str) -> List[str]:
    """
    Extrae el texto de requisitos (ignorando 'Effect:') y separa conjunciones (',' / 'and' / 'y')
    en cláusulas atómicas individuales para que el modelo 0.8B verifique 1 requisito por pregunta.
    """
    desc_clean = re.split(r"\b(?:effect|efecto)\s*:", description, flags=re.IGNORECASE)[0].strip()
    match = re.search(
        r"(?:requires|require|requiere|needs|need|only if|if|solo si|with a|with)\s*:?\s*(.+)",
        desc_clean,
        flags=re.IGNORECASE
    )
    req_text = match.group(1).strip() if match else desc_clean
    req_text = req_text.split(".")[0].strip()

    raw_clauses = re.split(r",|\band\b|\by\b|&", req_text, flags=re.IGNORECASE)
    clauses = [c.strip(" .") for c in raw_clauses if len(c.strip(" .")) > 1]
    return clauses if clauses else [req_text]


def get_actionable_entities_agnostic(entities: List[EntityHudDTO], inventory: List[str]) -> tuple[List[EntityHudDTO], List[EntityHudDTO], int]:
    actionable = []
    locked_devices = []
    total_tokens = 0

    active_useful = [
        e for e in entities
        if e.id in known_useful_ids and e.id not in completed_entity_ids
    ]

    devices_to_verify = []
    for e in active_useful:
        if e.id in entity_feedback_memory:
            locked_devices.append(e)
            continue

        role = entity_type_semantics_cache.get(e.type, "PICKUP_ITEM")
        if role == "PICKUP_ITEM":
            actionable.append(e)
        elif role == "ENVIRONMENT_DEVICE":
            desc_l = e.description.lower()
            requires_cond = any(w in desc_l for w in ["require", "requires", "requiere", "need", "needs", "must be", "only if", "solo si", "unlock with"])
            says_none = any(w in desc_l for w in ["requires: none", "no item", "requiere: nada", "requires: nothing"])
            if not requires_cond or says_none:
                actionable.append(e)
            else:
                devices_to_verify.append(e)

    if not devices_to_verify:
        return actionable, locked_devices, 0

    # Estado combinado: ítems en mano + estados almacenados/activados en la sala
    combined_items = list(inventory) + list(completed_states_memory)
    if not combined_items:
        locked_devices.extend(devices_to_verify)
        return actionable, locked_devices, 0

    held_only_str = ", ".join(inventory) if inventory else "Empty"
    stored_only_str = ", ".join(completed_states_memory) if completed_states_memory else "Empty"
    combined_str = ", ".join(combined_items)

    eval_state = {
        "held_inventory": held_only_str,
        "stored_and_activated_states": stored_only_str,
        "all_current_progress": combined_str
    }

    questions = {}
    device_clauses_map: Dict[str, List[str]] = {}

    for d in devices_to_verify:
        clauses = extract_atomic_requirements(d.description)
        device_clauses_map[d.id] = clauses
        desc_l = d.description.lower()

        # Si el dispositivo exige explícitamente que los objetos estén almacenados ("stored") o activados ("activated"),
        # comparamos contra stored_and_activated_states para obligar al agente a usar los slots/switches primero.
        needs_stored_state = any(w in desc_l for w in ["stored", "activated", "almacenado", "activado", "turned on"])
        target_pool_str = stored_only_str if needs_stored_state else combined_str

        if target_pool_str == "Empty":
            # Si requiere estados almacenados/activados y aún no hay ninguno, sabemos que no está listo sin gastar tokens
            device_clauses_map[d.id] = []
            locked_devices.append(d)
            continue

        for idx, clause in enumerate(clauses):
            q_key = f"can_use_{d.id}_{idx}"
            questions[q_key] = {
                "type": "choice",
                "instructions": (
                    f"Compare current state [{target_pool_str}] with the single requirement '{clause}' for '{d.type}'. "
                    f"Is '{clause}' present or satisfied in [{target_pool_str}]?"
                ),
                "criteria": {
                    "READY": f"YES. The required condition '{clause}' is present in [{target_pool_str}].",
                    "NOT_READY": f"NO. The required condition '{clause}' is missing from [{target_pool_str}]."
                }
            }

    if questions:
        try:
            ans, tok = run_inference(eval_state, questions)
            total_tokens += tok
            for d in devices_to_verify:
                clauses = device_clauses_map.get(d.id, [])
                if not clauses:
                    continue
                # Conjunción lógica exacta: TODAS las cláusulas atómicas deben dar READY
                all_ready = all(
                    ans.get(f"can_use_{d.id}_{idx}", {}).get("choice", "NOT_READY") == "READY"
                    for idx in range(len(clauses))
                )
                if all_ready:
                    actionable.append(d)
                else:
                    locked_devices.append(d)
        except Exception as err:
            print(f"[Device Verification Error] {err}")
            locked_devices.extend(devices_to_verify)

    return actionable, locked_devices, total_tokens

# =====================================================================
# 4. Q2 ESTRATÉGICO: SELECCIÓN DE META CON CONTEXTO DE OBJETIVOS BLOQUEADOS
# =====================================================================
def update_strategic_goal_react(
    state: UnifiedHudReActStateDTO,
    actionable_entities: List[EntityHudDTO],
    locked_devices: List[EntityHudDTO]
) -> tuple[str, str, float, int]:
    if not actionable_entities:
        return "EXPLORE", "EXPLORE", 1.0, 0

    # Si hay una única entidad lista (por ejemplo, la meta final ya desbloqueada), va directo (Conf: 1.00, 0 tokens)
    if len(actionable_entities) == 1:
        target = actionable_entities[0]
        return target.id, target.type, 1.0, 0

    inv_str = ", ".join(state.inventory) if state.inventory else "Empty"
    locked_str = "\n".join([f"- {d.id} ({d.type}): {d.description}" for d in locked_devices]) if locked_devices else "None"

    eval_state_q2 = {
        "mission": state.mission,
        "locked_goals_to_unlock": locked_str,
        "current_inventory": inv_str,
        "ready_targets": "\n".join([f"- {e.id} ({e.type}): {e.description}" for e in actionable_entities])
    }

    criteria = {}
    for e in actionable_entities:
        criteria[e.id] = f"Interact with {e.type} ({e.id}): {e.description}"

    q2 = {
        "decision": {
            "type": "choice",
            "instructions": (
                f"Mission: '{state.mission}'. Locked goals requirements: [{locked_str}]. "
                f"All listed ready_targets are accessible right now with current_inventory [{inv_str}]. "
                f"Which entity do you target to progress in the mission?"
            ),
            "criteria": criteria
        }
    }

    try:
        ans, tok = run_inference(eval_state_q2, q2)
        choice = ans.get("decision", {}).get("choice", actionable_entities[0].id)
        conf = ans.get("decision", {}).get("confidence", 0.0)

        target_ent = next((e for e in actionable_entities if e.id == choice), actionable_entities[0])
        return target_ent.id, target_ent.type, conf, tok
    except Exception as e:
        print(f"[Q2 Error] {e}")
        return actionable_entities[0].id, actionable_entities[0].type, 1.0, 0

# =====================================================================
# 5. PODA DE SENSORES PARA Q3 (NAVEGACIÓN EGOCÉNTRICA)
# =====================================================================
def build_pruned_q3_criteria(motor_actions: List[ActionOptionDTO], target_id: str) -> dict:
    criteria = {}
    if target_id != "EXPLORE":
        pattern = re.compile(rf"{re.escape(target_id)}\s*\([^)]+\)")
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
    current_mission_id = state.mission_id or "Mision_V8_Agnostica"

    # 1. Q1: Percepción semántica
    new_useful, new_useless, q1_tok, spotted_new_useful = classify_spotted_entities(state)

    # 2. ReAct: Evaluación de la acción anterior
    latest_obs, trigger_replan = evaluate_react_feedback(state)
    recent_observations.append(latest_obs)
    if len(recent_observations) > 3:
        recent_observations.pop(0)

    active_ids_in_world = {
        e.id for e in state.remembered_entities
        if e.id not in known_useless_ids and e.id not in completed_entity_ids
    }

    # 3. Disparador de replanificación
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
        actionable_entities, locked_devices, req_tok = get_actionable_entities_agnostic(state.remembered_entities, state.inventory)
        active_target_id, active_target_type, q2_conf, q2_tok = update_strategic_goal_react(state, actionable_entities, locked_devices)
        print(f"<color=cyan>[ReAct Meta] Obs: '{latest_obs}' -> Nueva Meta: {active_target_id} ({active_target_type}) [Conf: {q2_conf:.2f}]</color>")

    # 4. Disparo directo en mira si ya alcanzamos físicamente la meta
    if active_target_id != "EXPLORE":
        interact_code = f"INTERACT:{active_target_id}"
        if any(a.code == interact_code for a in state.available_actions):
            lat = round(time.time() - start_time, 2)
            total_tok = q1_tok + req_tok + q2_tok
            last_action_taken = interact_code
            thought = f"[ReAct-V8|{total_tok}t] Target Reached: {active_target_id} => {interact_code}"
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
        thought = f"[ReAct-V8-Auto|{total_tok}t] Goal: {active_target_id} => {chosen_code}"
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

    pruned_criteria = build_pruned_q3_criteria(motor_actions, active_target_id)
    eval_state_q3 = {"current_target": active_target_id}
    q3 = {
        "step": {
            "type": "choice",
            "instructions": (
                f"Select the action that leads directly to target {active_target_id}."
                if active_target_id != "EXPLORE"
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

    if active_target_id != "EXPLORE":
        direct_matches = [code for code, desc in pruned_criteria.items() if desc.startswith("Leads directly to target")]
        if len(direct_matches) == 1 and chosen_code != direct_matches[0]:
            chosen_code = direct_matches[0]

    total_tok = q1_tok + req_tok + q2_tok + q3_tok
    lat = round(time.time() - start_time, 2)
    last_action_taken = chosen_code

    thought = (
        f"[ReAct-V8|{total_tok}t] Obs: '{latest_obs}' | "
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
    filename = "./versiones/Voyager_Architecture_v8_Agnostica.json"
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