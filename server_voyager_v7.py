import time
import requests
import json
import os
import statistics
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import List, Dict

# --- CONFIGURACIÓN V7.2: VISUAL MAXIMIZATION + TOLERANT DTOs ---
DECISION_ENGINE = "KEV"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

ARCHITECTURE_TAG = f"Continuous_ReAct_V7_{DECISION_ENGINE}"

current_test_decisions = []
current_mission_id = "Mision_V7_Continua"

# --- MEMORIA COGNITIVA PERSISTENTE ---
active_target_id = "EXPLORE"
active_target_type = "EXPLORE"

known_useful_ids = set()
known_useless_ids = set()
completed_entity_ids = set()

entity_type_semantics_cache: Dict[str, str] = {}
entity_feedback_memory: Dict[str, str] = {}
recent_observations: List[str] = []

last_inventory: List[str] = []
last_remembered_ids = set()
last_action_taken = "NONE"
last_turn_taken = "KEEP_0"

# --- DTOs CON VALORES POR DEFECTO PARA PREVENIR ERRORES 422 ---
class RememberedEntityRadarDTO(BaseModel):
    id: str = ""
    type: str = ""
    description: str = ""
    distance_m: float = 0.0
    relative_angle_deg: float = 0.0
    bearing_sector: str = "KEEP_0"
    has_line_of_sight: bool = True
    in_interact_range: bool = False
    sector: str = "NONE" # Compatibilidad hacia atrás
    steps: int = 99      # Compatibilidad hacia atrás

class AngularSensorSectorDTO(BaseModel):
    sector_code: str = "KEEP_0"
    angle_deg: float = 0.0
    clearance_m: float = 10.0
    is_blocked: bool = False
    visual_depth_m: float = 10.0
    unseen_volume: float = 5.0
    unvisited_score: float = 5.0 # Compatibilidad hacia atrás
    obstacle_name: str = "NONE"
    gains_los_to: List[str] = Field(default_factory=list)

class ContinuousStateDTO(BaseModel):
    mission_id: str = "Mision_V7_Continua"
    mission: str = ""
    inventory: List[str] = Field(default_factory=list)
    last_observation: str = ""
    agent_x: float = 0.0
    agent_z: float = 0.0
    agent_yaw_deg: float = 0.0
    last_turn_code: str = "KEEP_0"
    last_move_code: str = "NONE"
    crosshair_entity_id: str = "NONE"
    newly_spotted: List[RememberedEntityRadarDTO] = Field(default_factory=list)
    remembered_entities: List[RememberedEntityRadarDTO] = Field(default_factory=list)
    angular_sensors: List[AngularSensorSectorDTO] = Field(default_factory=list)
    available_actions: list = Field(default_factory=list)

class BenchmarkEnd(BaseModel):
    duracion_total: float = 0.0
    timed_out: bool = False

def estimate_tokens(state_str: str, questions_obj: dict) -> int:
    raw = f"{state_str} {json.dumps(questions_obj, ensure_ascii=False)}"
    opts_count = sum(len(q.get("criteria", {})) for q in questions_obj.values())
    return int(len(raw) / 3.8) + 4 + (opts_count * 2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"\n[Voyager V7.2] Motor: {DECISION_ENGINE} | Navegación Guiada por Cobertura Visual.")
    yield

app = FastAPI(title="Voyager V7.2 - Visual Maximization Spatial Planner", lifespan=lifespan)

# Capturador de errores de validación para depurar en consola
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"[422 Validation Error] Campos con conflicto: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, active_target_id, active_target_type
    global known_useful_ids, known_useless_ids, completed_entity_ids
    global entity_type_semantics_cache, entity_feedback_memory, recent_observations
    global last_inventory, last_remembered_ids, last_action_taken, last_turn_taken

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
    last_turn_taken = "KEEP_0"
    return {"status": "cleared"}

def run_inference(state_dict: dict, questions: dict) -> tuple[dict, int]:
    state_str = json.dumps(state_dict, ensure_ascii=False)
    tokens_used = estimate_tokens(state_str, questions)
    payload = {"state": state_str, "model": "kev-0.8b", "questions": questions}
    res = requests.post(KEV_URL, json=payload).json()
    if "usage" in res and "input_tokens" in res["usage"]:
        tokens_used = res["usage"]["input_tokens"]
    return res.get("answers", {}), tokens_used

@app.post("/evaluate_entities")
async def evaluate_entities(request: Request):
    global known_useful_ids, known_useless_ids, entity_type_semantics_cache
    try:
        body = await request.json()
    except Exception:
        return {"useful_ids": [], "useless_ids": [], "tokens_used": 0}

    items = body if isinstance(body, list) else (body.get("entities") or body.get("newly_spotted") or [])
    useful_ids, useless_ids = [], []
    total_tok = 0

    for item in items:
        ent_id = item.get("id", "")
        ent_type = item.get("type", "")
        ent_desc = item.get("description", "")
        if not ent_id or not ent_type:
            continue

        if ent_type in entity_type_semantics_cache:
            role = entity_type_semantics_cache[ent_type]
        else:
            q1_state = {"entity": ent_type, "description": ent_desc}
            q1 = {
                "role": {
                    "type": "choice",
                    "instructions": f"Analyze '{ent_type}' ({ent_desc}). What functional role does it represent?",
                    "criteria": {
                        "PICKUP_ITEM": "Loose portable item on ground to collect into inventory.",
                        "ENVIRONMENT_DEVICE": "Stationary structure, door, or terminal to operate/unlock.",
                        "USELESS_SCRAP": "Inert junk, scrap metal, or debris."
                    }
                }
            }
            try:
                ans, tok = run_inference(q1_state, q1)
                role = ans.get("role", {}).get("choice", "PICKUP_ITEM")
                total_tok += tok
            except Exception:
                role = "PICKUP_ITEM"
            entity_type_semantics_cache[ent_type] = role

        if role != "USELESS_SCRAP":
            known_useful_ids.add(ent_id)
            useful_ids.append(ent_id)
        else:
            known_useless_ids.add(ent_id)
            useless_ids.append(ent_id)

    return {"useful_ids": useful_ids, "useless_ids": useless_ids, "tokens_used": total_tok}

# =====================================================================
# 1. EVALUADOR DE EVENTOS ReAct
# =====================================================================
def evaluate_react_feedback(state: ContinuousStateDTO) -> tuple[str, bool]:
    global last_inventory, last_remembered_ids, entity_feedback_memory, completed_entity_ids

    current_ids = set(e.id for e in state.remembered_entities if e.id not in known_useless_ids)
    new_items = [item for item in state.inventory if item not in last_inventory]
    consumed_ids = last_remembered_ids - current_ids

    unity_obs = state.last_observation.strip() if state.last_observation else ""
    trigger_replan = False

    if last_action_taken == "NONE":
        obs = unity_obs or "OBSERVATION: Mission started."
        trigger_replan = True
    elif last_action_taken.startswith("INTERACT:"):
        target_id = last_action_taken.split(":")[1]
        trigger_replan = True
        if "SUCCESS" in unity_obs or new_items or (target_id in consumed_ids) or len(consumed_ids) > 0:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} SUCCEEDED!"
            completed_entity_ids.add(target_id)
            if len(new_items) > 0:
                entity_feedback_memory.clear()
        else:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} FAILED!"
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
# 2. Q1: PERCEPCIÓN SEMÁNTICA
# =====================================================================
def classify_spotted_entities(state: ContinuousStateDTO) -> tuple[List[str], List[str], int, bool]:
    new_useful, new_useless = [], []
    total_tok = 0
    found_new_useful = False

    for spotted in state.newly_spotted:
        if spotted.type in entity_type_semantics_cache:
            role = entity_type_semantics_cache[spotted.type]
        else:
            q1_state = {"entity": spotted.type, "description": spotted.description}
            q1 = {
                "role": {
                    "type": "choice",
                    "instructions": f"Analyze '{spotted.type}' ({spotted.description}). What functional role does it represent?",
                    "criteria": {
                        "PICKUP_ITEM": "Loose portable item on ground to collect into inventory.",
                        "ENVIRONMENT_DEVICE": "Stationary structure, door, or terminal to operate/unlock.",
                        "USELESS_SCRAP": "Inert junk, scrap metal, or debris."
                    }
                }
            }
            try:
                ans, tok = run_inference(q1_state, q1)
                role = ans.get("role", {}).get("choice", "PICKUP_ITEM")
                total_tok += tok
            except Exception:
                role = "PICKUP_ITEM"

            entity_type_semantics_cache[spotted.type] = role

        if role != "USELESS_SCRAP":
            known_useful_ids.add(spotted.id)
            new_useful.append(spotted.id)
            found_new_useful = True
        else:
            known_useless_ids.add(spotted.id)
            new_useless.append(spotted.id)

    return new_useful, new_useless, total_tok, found_new_useful

# =====================================================================
# 3. VERIFICACIÓN DE REQUISITOS Y Q2 ESTRATÉGICO
# =====================================================================
def get_actionable_entities_model(entities: List[RememberedEntityRadarDTO], inventory: List[str]) -> tuple[List[RememberedEntityRadarDTO], int]:
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
        if role == "PICKUP_ITEM":
            actionable.append(e)
        elif role == "ENVIRONMENT_DEVICE":
            devices_to_verify.append(e)

    if devices_to_verify:
        eval_state = {
            "held_inventory": inv_str,
            "target_devices": "\n".join([f"- {d.id} ({d.type}): {d.description}" for d in devices_to_verify])
        }
        questions = {}
        for d in devices_to_verify:
            questions[f"can_use_{d.id}"] = {
                "type": "choice",
                "instructions": f"Does held_inventory [{inv_str}] contain the item required to use '{d.type}' ({d.description})?",
                "criteria": {
                    "READY": f"YES. Item for {d.type} is in inventory [{inv_str}].",
                    "NOT_READY": f"NO. Missing item required for {d.type}."
                }
            }
        try:
            ans, tok = run_inference(eval_state, questions)
            total_tokens += tok
            for d in devices_to_verify:
                if ans.get(f"can_use_{d.id}", {}).get("choice", "NOT_READY") == "READY":
                    actionable.append(d)
        except Exception as err:
            print(f"[Device Verification Error] {err}")

    return actionable, total_tokens

def select_strategic_goal_model(state: ContinuousStateDTO, actionable_entities: List[RememberedEntityRadarDTO]) -> tuple[str, str, float, int]:
    """
    Decisión estratégica tomada 100% por el modelo KEV (sin bypass de código).
    Plantea un contraste inequívoco entre recoger un objeto ya disponible o continuar explorando.
    """
    inv_str = ", ".join(state.inventory) if state.inventory else "Empty (hands free)"
    
    if not actionable_entities:
        return "EXPLORE", "EXPLORE", 1.0, 0

    eval_state_q2 = {
        "mission": state.mission,
        "current_inventory": inv_str,
        "spotted_ready_targets": "\n".join([f"- {e.id} ({e.type}): {e.description}" for e in actionable_entities])
    }

    # Criterios con contraste funcional directo para KEV
    criteria = {}
    for e in actionable_entities:
        if "Keycard" in e.type or "Data" in e.type:
            criteria[e.id] = f"COLLECT {e.type} ({e.id}) immediately into your inventory."
        else:
            criteria[e.id] = f"OPERATE {e.type} ({e.id}) right now using your current inventory [{inv_str}]."

    criteria["EXPLORE"] = "PASS / IGNORE targets: Do not interact with any of the spotted ready targets and wander into other areas."

    q2 = {
        "decision": {
            "type": "choice",
            "instructions": (
                f"You have discovered: [{', '.join(e.id for e in actionable_entities)}]. "
                f"They are ready and accessible right now with your inventory [{inv_str}]. "
                f"Do you pick up/operate an available target to progress, or ignore them to EXPLORE?"
            ),
            "criteria": criteria
        }
    }

    try:
        ans, tok = run_inference(eval_state_q2, q2)
        choice = ans.get("decision", {}).get("choice", "EXPLORE")
        conf = ans.get("decision", {}).get("confidence", 0.0)
        
        if choice == "EXPLORE":
            return "EXPLORE", "EXPLORE", conf, tok
            
        target_ent = next((e for e in actionable_entities if e.id == choice), None)
        if target_ent:
            return target_ent.id, target_ent.type, conf, tok
        return actionable_entities[0].id, actionable_entities[0].type, conf, tok
    except Exception as err:
        print(f"[Q2 Error] {err}")
        return actionable_entities[0].id, actionable_entities[0].type, 1.0, 0

# =====================================================================
# 4. Q3 DUAL: MAXIMIZACIÓN VISUAL Y EVITACIÓN DE OBSTÁCULOS
# =====================================================================
# =====================================================================
# 4. Q3 DUAL: NAVEGACIÓN PRECISA A META Y MAXIMIZACIÓN VISUAL EN EXPLORACIÓN
# =====================================================================
def decide_continuous_navigation_model(
    state: ContinuousStateDTO,
    target_id: str,
    target_type: str,
    prev_turn: str,
    prev_move: str
) -> tuple[str, float, str, float, float, int]:
    target_ent = next((e for e in state.remembered_entities if e.id == target_id), None)
    sector_map: Dict[str, AngularSensorSectorDTO] = {s.sector_code: s for s in state.angular_sensors}

    # Descriptores sensoriales objetivos para el estado cognitivo
    sensors_desc = []
    for s in state.angular_sensors:
        sightline = s.visual_depth_m if s.visual_depth_m > 0 else s.clearance_m
        obs_tag = f"obstacle ({s.obstacle_name}) at {s.clearance_m:.1f}m" if s.is_blocked else f"{s.clearance_m:.1f}m free"
        sensors_desc.append(f"{s.sector_code} ({s.angle_deg:+.0f}°): {obs_tag}, sightline={sightline:.1f}m")

    # -------------------------------------------------------------
    # CASO A: TENEMOS UNA META ACTIVA CONCRETA (Keycard, LockedDoor, etc.)
    # -------------------------------------------------------------
    if target_ent:
        goal_summary = (
            f"TARGET: {target_ent.id} ({target_ent.type}) located at angle {target_ent.relative_angle_deg:+.0f}° "
            f"({target_ent.bearing_sector}), distance {target_ent.distance_m:.1f}m, "
            f"in_interact_range={'YES' if target_ent.in_interact_range else 'NO'}."
        )

        spatial_state = {
            "active_goal": goal_summary,
            "previous_action": f"Turned {prev_turn} and executed {prev_move}",
            "surrounding_sensors": " | ".join(sensors_desc)
        }

        # 1. Encontrar el sector angular con la menor desviación respecto al objetivo
        def angle_diff(s_deg: float, t_deg: float) -> float:
            d = abs(t_deg - s_deg)
            return 360 - d if d > 180 else d

        unblocked_sectors = [s for s in state.angular_sensors if not s.is_blocked]
        best_sector_code = "KEEP_0"
        if unblocked_sectors:
            best_sector_code = min(unblocked_sectors, key=lambda s: angle_diff(s.angle_deg, target_ent.relative_angle_deg)).sector_code
        elif state.angular_sensors:
            best_sector_code = min(state.angular_sensors, key=lambda s: angle_diff(s.angle_deg, target_ent.relative_angle_deg)).sector_code

        # 2. Criterios de giro con alto contraste focalizados en el objetivo
        turn_criteria = {}
        for s in state.angular_sensors:
            diff = angle_diff(s.angle_deg, target_ent.relative_angle_deg)
            
            if s.is_blocked:
                # Si está bloqueado pero es la entidad meta dentro de rango de alcance
                if target_ent.in_interact_range and s.sector_code == best_sector_code:
                    turn_criteria[s.sector_code] = (
                        f"Turn {s.angle_deg:+.0f}°: DIRECT ALIGNMENT - Directly faces {target_ent.id} within reach."
                    )
                else:
                    turn_criteria[s.sector_code] = (
                        f"Turn {s.angle_deg:+.0f}°: BLOCKED by {s.obstacle_name} at {s.clearance_m:.1f}m. Avoid."
                    )
                continue

            if s.sector_code == best_sector_code:
                if target_ent.has_line_of_sight:
                    turn_criteria[s.sector_code] = (
                        f"Turn {s.angle_deg:+.0f}°: DIRECT ALIGNMENT (Best Choice) - Aligns camera straight toward "
                        f"target {target_ent.id} (offset {diff:.0f}°, distance {target_ent.distance_m:.1f}m)."
                    )
                else:
                    turn_criteria[s.sector_code] = (
                        f"Turn {s.angle_deg:+.0f}°: BEST DETOUR - Clearest open path ({s.clearance_m:.1f}m) to approach "
                        f"{target_ent.id} around walls."
                    )
            elif target_ent.id in s.gains_los_to:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: WALL BYPASS - Open detour ({s.clearance_m:.1f}m) that gains line of sight to {target_ent.id}."
                )
            elif s.sector_code == "BACK_180":
                turn_criteria[s.sector_code] = f"Turn +180°: RETREAT - Turns completely away from target {target_ent.id}."
            else:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: WRONG DIRECTION - Diverges {diff:.0f}° away from target {target_ent.id}."
                )

        # 3. Criterios de locomoción hacia el objetivo
        move_criteria = {}
        if target_ent.in_interact_range:
            move_criteria[f"INTERACT:{target_ent.id}"] = (
                f"INTERACT NOW with {target_ent.id} ({target_ent.type}) - Target is right in front of you within reach ({target_ent.distance_m:.1f}m)."
            )
            move_criteria["SCAN_IN_PLACE"] = f"Wait in place without interacting with {target_ent.id}."
            locomotion_instruction = f"Target {target_ent.id} is within physical reach at {target_ent.distance_m:.1f}m. What action do you execute?"
        else:
            if target_ent.distance_m > 2.6:
                move_criteria["ADVANCE_FLUID"] = (
                    f"ADVANCE FLUID (2.6m) - Close distance rapidly along chosen direction toward {target_ent.id} ({target_ent.distance_m:.1f}m away)."
                )
                move_criteria["ADVANCE_CAREFUL"] = (
                    f"ADVANCE CAREFUL (1.1m) - Slow step toward {target_ent.id}."
                )
            else:
                # Aproximación de precisión para entrar al rango de interacción (<= 2.4m) sin pasarse
                move_criteria["ADVANCE_CAREFUL"] = (
                    f"ADVANCE CAREFUL (1.1m) - Controlled approach step to enter interaction range of {target_ent.id} ({target_ent.distance_m:.1f}m away)."
                )
                move_criteria["ADVANCE_FLUID"] = (
                    f"ADVANCE FLUID (2.6m) - Too fast: may overshoot or run past target {target_ent.id}."
                )
            move_criteria["SCAN_IN_PLACE"] = "Do not walk (0.0m); rotate camera only to orient toward target."
            locomotion_instruction = f"Target {target_ent.id} is {target_ent.distance_m:.1f}m away. How do you move along the chosen direction to reach it?"

        questions = {
            "camera_turn": {
                "type": "choice",
                "instructions": (
                    f"Your active target is {target_ent.id} ({target_ent.type}) located at angle "
                    f"{target_ent.relative_angle_deg:+.0f}° ({target_ent.bearing_sector}), {target_ent.distance_m:.1f}m away. "
                    f"Which camera turn aligns directly toward your target?"
                ),
                "criteria": turn_criteria
            },
            "locomotion": {
                "type": "choice",
                "instructions": locomotion_instruction,
                "criteria": move_criteria
            }
        }

    # -------------------------------------------------------------
    # CASO B: MODO EXPLORE (Búsqueda y Maximización Visual)
    # -------------------------------------------------------------
    else:
        goal_summary = "TARGET: EXPLORE unknown areas. Maximize visual coverage into deep room spaces and avoid walls."
        spatial_state = {
            "active_goal": goal_summary,
            "previous_action": f"Turned {prev_turn} and executed {prev_move}",
            "spatial_vision": " | ".join(sensors_desc)
        }

        turn_criteria = {}
        for s in state.angular_sensors:
            sightline = s.visual_depth_m if s.visual_depth_m > 0 else s.clearance_m

            if s.is_blocked:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: BLOCKED - Faces {s.obstacle_name} at {s.clearance_m:.1f}m. Sightline obstructed."
                )
            elif s.sector_code == "BACK_180":
                if prev_turn == "BACK_180":
                    turn_criteria["BACK_180"] = "Turn +180°: FORBIDDEN U-TURN - Repeating oscillation. Do NOT select."
                else:
                    turn_criteria["BACK_180"] = "Turn +180°: RETREAT - Stares back at already visited ground."
            elif sightline >= 8.0:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: DEEP OPEN VISTA ({sightline:.1f}m sightline, {s.clearance_m:.1f}m walkable). "
                    f"Looks straight into the wide open center of the room to discover new areas and objects."
                )
            elif sightline >= 4.0:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: MODERATE VIEW ({sightline:.1f}m sightline, {s.clearance_m:.1f}m walkable)."
                )
            else:
                turn_criteria[s.sector_code] = (
                    f"Turn {s.angle_deg:+.0f}°: SHALLOW VIEW (only {sightline:.1f}m sightline) near walls."
                )

        move_criteria = {
            "ADVANCE_FLUID": "Advance fluidly 2.6m forward into wide open space (Best for open corridors).",
            "ADVANCE_CAREFUL": "Advance carefully 1.1m forward (Best near corners or walls).",
            "SCAN_IN_PLACE": "Rotate camera in place without walking (0.0m) to inspect the area."
        }

        questions = {
            "camera_turn": {
                "type": "choice",
                "instructions": (
                    f"You are exploring. Your active goal is [{goal_summary}]. "
                    f"Select the camera turn that points toward deep open space with maximum visual coverage."
                ),
                "criteria": turn_criteria
            },
            "locomotion": {
                "type": "choice",
                "instructions": "Select the movement action along the chosen camera direction to explore safely.",
                "criteria": move_criteria
            }
        }

    # Inferencia del modelo
    try:
        ans, tok = run_inference(spatial_state, questions)
        chosen_turn = ans.get("camera_turn", {}).get("choice", "KEEP_0")
        turn_conf = ans.get("camera_turn", {}).get("confidence", 0.0)

        chosen_move = ans.get("locomotion", {}).get("choice", "ADVANCE_FLUID")
        move_conf = ans.get("locomotion", {}).get("confidence", 0.0)
        avg_conf = (turn_conf + move_conf) / 2.0
    except Exception as err:
        print(f"[Q3 Dual Error] {err}")
        chosen_turn = "KEEP_0"
        chosen_move = "SCAN_IN_PLACE"
        avg_conf = 0.0
        tok = 0

    turn_angle = sector_map[chosen_turn].angle_deg if chosen_turn in sector_map else 0.0

    if chosen_move.startswith("INTERACT:"):
        move_dist = 0.0
    elif chosen_move == "ADVANCE_FLUID":
        move_dist = 2.6
    elif chosen_move == "ADVANCE_CAREFUL":
        move_dist = 1.1
    else:
        move_dist = 0.0

    return chosen_turn, turn_angle, chosen_move, move_dist, avg_conf, tok

# =====================================================================
# 5. ENDPOINT PRINCIPAL DE PLANIFICACIÓN
# =====================================================================
@app.post("/plan_action")
async def plan_action(state: ContinuousStateDTO):
    global current_mission_id, active_target_id, active_target_type
    global last_action_taken, last_turn_taken, recent_observations
    start_time = time.time()
    current_mission_id = state.mission_id or "Mision_V7_Continua"

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

    # 3. Q2: Selección de meta estratégica
    should_run_q2 = (
        trigger_replan
        or spotted_new_useful
        or (active_target_id != "EXPLORE" and active_target_id not in active_ids_in_world)
        or (active_target_id in entity_feedback_memory)
    )

    q2_tok = 0
    req_tok = 0
    q2_conf = 1.0

    if should_run_q2:
        actionable_entities, req_tok = get_actionable_entities_model(state.remembered_entities, state.inventory)
        active_target_id, active_target_type, q2_conf, q2_tok = select_strategic_goal_model(state, actionable_entities)
        print(f"[ReAct Meta V7] Obs: '{latest_obs}' -> Meta elegida: {active_target_id} ({active_target_type}) [Conf: {q2_conf:.2f}]")

    # 4. Q3 Dual con maximización visual
    turn_code, turn_angle, move_code, move_dist, q3_conf, q3_tok = decide_continuous_navigation_model(
        state, active_target_id, active_target_type, state.last_turn_code, state.last_move_code
    )

    total_tok = q1_tok + req_tok + q2_tok + q3_tok
    lat = round(time.time() - start_time, 2)
    last_turn_taken = turn_code
    last_action_taken = move_code if move_code.startswith("INTERACT:") else f"{turn_code}+{move_code}"

    thought = (
        f"[V7.2-Vision|{total_tok}t] Goal: {active_target_id}({active_target_type}) | "
        f"Turn: {turn_code}({turn_angle:+.0f}°) + Move: {move_code}({move_dist:.1f}m) [Conf: {q3_conf:.2f}]"
    )

    current_test_decisions.append({
        "observacion": latest_obs,
        "meta_activa": f"{active_target_id}({active_target_type})",
        "pensamiento": thought,
        "latencia": lat,
        "tokens_entrada": total_tok,
        "giro": turn_code,
        "movimiento": move_code
    })

    return {
        "thought": thought,
        "target_goal": active_target_id,
        "turn_code": turn_code,
        "turn_angle_deg": turn_angle,
        "move_code": move_code,
        "move_distance_m": move_dist,
        "latency_seconds": lat,
        "useful_ids": new_useful,
        "useless_ids": new_useless
    }

@app.post("/end_benchmark")
async def end_benchmark(data: BenchmarkEnd):
    global current_test_decisions, current_mission_id
    filename = "./versiones/Voyager_Architecture_v7_Continua.json"
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