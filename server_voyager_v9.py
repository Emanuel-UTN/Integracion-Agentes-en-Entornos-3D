import time
import requests
import json
import os
import re
import statistics
from datetime import datetime
from fastapi import FastAPI
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Dict, Optional

# --- CONFIGURACIÓN V9: SAFE CROSS-TASK GENERALIZABLE MEMORY + SPL ---
DECISION_ENGINE = "KEV"
KEV_URL = "http://127.0.0.1:8009/v1/systemone"

USE_OLLAMA_REFLECTOR = True
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_REFLECTOR_MODEL = "qwen2.5:3b"

ARCHITECTURE_TAG = f"SafeGeneralizable_ReAct_V9_{DECISION_ENGINE}"
GENERAL_MEMORY_FILE = "./memoria/general_memory_v9.json"

STOPWORDS = {"a", "an", "the", "un", "una", "unos", "unas", "el", "la", "los", "las", "of", "de", "to", "in", "on", "and"}

current_test_decisions = []
current_mission_id = "Mision_V9_Generalizable"
current_mission_text = ""
current_ideal_steps = 0

# --- MEMORIA DE CORTO PLAZO (EPISODIO ACTUAL) ---
active_target_id = "EXPLORE"
active_target_type = "EXPLORE"

known_useful_ids = set()
known_useless_ids = set()
completed_entity_ids = set()
completed_states_memory: List[str] = []

episode_entities_catalog: Dict[str, Dict[str, str]] = {}
episode_applied_rule_ids = set()

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
    mission_id: str = "Mision_V9_Generalizable"
    mission: str = ""
    ideal_steps: int = 0
    inventory: List[str] = []
    last_observation: str = ""
    crosshair_entity_id: str = "NONE"
    newly_spotted: List[EntityHudDTO] = []
    remembered_entities: List[EntityHudDTO] = []
    available_actions: List[ActionOptionDTO] = []


class BenchmarkEnd(BaseModel):
    duracion_total: float
    timed_out: bool = False
    ideal_steps: int = 0
    pasos_unity: int = 0


def estimate_tokens(state_str: str, questions_obj: dict) -> int:
    raw = f"{state_str} {json.dumps(questions_obj, ensure_ascii=False)}"
    opts_count = sum(len(q.get("criteria", {})) for q in questions_obj.values())
    return int(len(raw) / 3.8) + 4 + (opts_count * 2)


def humanize_label(text: str) -> str:
    """Normaliza 'Metal_bar' -> 'Metal bar' para evitar fallos de matching léxico en 0.8B."""
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip()


def strip_leading_articles(phrase: str) -> str:
    return re.sub(r"^(?:a|an|the|un|una|unos|unas|el|la|los|las)\s+", "", phrase.strip(), flags=re.IGNORECASE).strip()


def has_unexplored_environment(available_actions: List[ActionOptionDTO]) -> bool:
    """Verifica en los sensores motores de Unity si aún quedan áreas o celdas por explorar."""
    return any(
        ("Unexplored" in a.status or "Scan" in a.status)
        for a in available_actions
    )


# =====================================================================
# GESTIÓN DE MEMORIA GENERALIZABLE SEGURA (ESCALADO + ROLLBACK)
# =====================================================================
def load_general_memory() -> dict:
    os.makedirs(os.path.dirname(GENERAL_MEMORY_FILE), exist_ok=True)
    if os.path.exists(GENERAL_MEMORY_FILE) and os.path.getsize(GENERAL_MEMORY_FILE) > 0:
        try:
            with open(GENERAL_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("principles", [])
                data.setdefault("episode_history", [])
                return data
        except Exception as err:
            print(f"[General Memory Read Error] {err}")
    return {"principles": [], "episode_history": []}


def save_general_memory(mem: dict) -> None:
    os.makedirs(os.path.dirname(GENERAL_MEMORY_FILE), exist_ok=True)
    with open(GENERAL_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=4, ensure_ascii=False)


def normalize_tokens(text: str) -> set:
    tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    return tokens - STOPWORDS


def is_rule_domain_agnostic(rule_text: str, catalog: Dict[str, Dict[str, str]]) -> bool:
    rule_l = rule_text.lower()
    if "useless_scrap" in rule_l and any(w in rule_l for w in ["unrelated", "redundant", "not required"]):
        return False
    for eid, info in catalog.items():
        if eid.lower() in rule_l:
            return False
        etype = info.get("type", "").lower()
        if len(etype) > 4 and etype in rule_l:
            return False
    return True


def upsert_or_escalate_principle(
    mem: dict,
    failure_signature: str,
    category: str,
    context_tag: str,
    priority_tier: int,
    rule_variants: List[str],
    source_mission: str,
    initial_weight: float = 1.15
) -> tuple[Optional[str], Optional[str]]:
    clean_variants = [v.strip() for v in rule_variants if v.strip()]
    existing = next(
        (p for p in mem["principles"] if p.get("failure_signature") == failure_signature),
        None
    )

    if not existing:
        chosen_text = clean_variants[0]
        new_id = f"principle_{len(mem['principles']) + 1}"
        mem["principles"].append({
            "id": new_id,
            "failure_signature": failure_signature,
            "escalation_level": 0,
            "category": category,
            "context_tag": context_tag,
            "priority_tier": priority_tier,
            "rule_text": chosen_text,
            "rule_variants": clean_variants,
            "weight": initial_weight,
            "active": True,
            "reinforcements": 1,
            "regressions": 0,
            "learned_from": source_mission,
            "updated_at": datetime.now().isoformat(timespec="seconds")
        })
        return chosen_text, None

    if not existing.get("active", True):
        return None, None

    stored_variants = existing.get("rule_variants") or clean_variants
    existing["rule_variants"] = stored_variants
    curr_level = existing.get("escalation_level", 0)

    if curr_level + 1 < len(stored_variants):
        next_level = curr_level + 1
        escalated_text = stored_variants[next_level]
        existing["escalation_level"] = next_level
        existing["rule_text"] = escalated_text
        existing["weight"] = round(min(2.0, max(existing.get("weight", 1.0), 1.05) + 0.15), 2)
        existing["reinforcements"] = existing.get("reinforcements", 1) + 1
        existing["learned_from"] = source_mission
        existing["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return escalated_text, None

    return None, None


def get_top_principle(category: str, context_tags: List[str]) -> Optional[dict]:
    mem = load_general_memory()
    candidates = [
        p for p in mem.get("principles", [])
        if p.get("active", True)
        and p.get("category") == category
        and p.get("context_tag") in context_tags
        and p.get("weight", 1.0) >= 0.85
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda x: (-x.get("priority_tier", 2), x.get("weight", 1.0)),
        reverse=True
    )
    return candidates[0]


def abstract_failure_with_local_llm(
    timed_out: bool,
    spl: float,
    diagnostic_notes: List[str],
    catalog: Dict[str, Dict[str, str]]
) -> Optional[str]:
    if not USE_OLLAMA_REFLECTOR or not diagnostic_notes:
        return None
    prompt = (
        f"An AI agent played a 3D puzzle task.\n"
        f"Outcome: timed_out={timed_out}, SPL_efficiency={spl:.2f}\n"
        f"Mistakes observed: {'; '.join(diagnostic_notes[:3])}\n"
        f"Write ONE general, domain-independent strategic rule (max 15 words) about prerequisite ordering or operating workstations before final goals. "
        f"CRITICAL: NEVER mention specific object names, IDs, or words from this mission, and NEVER tell the agent to ignore items as scrap. Plain English only."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_REFLECTOR_MODEL, "prompt": prompt, "stream": False},
            timeout=4.0
        )
        if resp.status_code == 200:
            raw_rule = resp.json().get("response", "").strip().splitlines()[0].strip(' "\'')
            if 5 <= len(raw_rule.split()) <= 18 and is_rule_domain_agnostic(raw_rule, catalog):
                return raw_rule
    except Exception:
        pass
    return None


def distill_general_experience(
    mission_id: str,
    mission_text: str,
    decisions: List[dict],
    success: bool,
    timed_out: bool,
    total_steps: int,
    ideal_steps: int,
    spl: float
) -> tuple[List[str], List[str]]:
    mem = load_general_memory()
    new_principles = []
    reinforced_principles = []

    mission_runs = [
        h for h in mem.get("episode_history", [])
        if h.get("mission_id") == mission_id
    ]
    prev_successes = [h for h in mission_runs if h.get("success", False)]
    best_prev_spl = max((h["spl"] for h in prev_successes), default=0.0)
    last_prev_spl = mission_runs[-1]["spl"] if mission_runs else 0.0

    is_regression = (
        (timed_out and len(prev_successes) > 0)
        or (success and last_prev_spl > 0 and spl < last_prev_spl - 0.03)
    )
    is_genuine_improvement = (
        success and (spl > best_prev_spl + 0.01 or (spl >= 0.90 and len(episode_applied_rule_ids) > 0))
    )

    # 1. Gestión de pesos y escalado inteligente antes de desactivar en regresiones
    for p in mem.get("principles", []):
        if p["id"] in episode_applied_rule_ids and p.get("active", True):
            if is_regression:
                p["regressions"] = p.get("regressions", 0) + 1
                variants = p.get("rule_variants", [])
                curr_lvl = p.get("escalation_level", 0)

                if timed_out and len(prev_successes) > 0:
                    p["weight"] = round(p.get("weight", 1.0) - 0.65, 2)
                    p["active"] = False
                    print(f"[Rollback Memoria V9] Principio desactivado por timeout: '{p['rule_text']}'")
                elif curr_lvl + 1 < len(variants):
                    # Escalamos a una variante más precisa en lugar de apagar el aprendizaje
                    next_lvl = curr_lvl + 1
                    p["escalation_level"] = next_lvl
                    p["rule_text"] = variants[next_lvl]
                    p["weight"] = 1.05
                    p["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    new_principles.append(f"[Escalated-{p['failure_signature']}] {p['rule_text']}")
                    print(f"[Escalado Memoria V9] Principio refinado tras regresión: '{p['rule_text']}'")
                else:
                    p["weight"] = round(p.get("weight", 1.0) - 0.45, 2)
                    if p["weight"] < 0.85:
                        p["active"] = False
                        print(f"[Rollback Memoria V9] Principio desactivado tras agotar niveles: '{p['rule_text']}'")
            elif is_genuine_improvement:
                p["weight"] = round(min(2.0, p.get("weight", 1.0) + 0.20), 2)
                p["reinforcements"] = p.get("reinforcements", 1) + 1
                reinforced_principles.append(p["rule_text"])

    diagnostic_notes = []
    failed_interactions = []
    explore_steps = 0
    explored_with_nothing_left = 0

    for dec in decisions:
        act = dec.get("decision", "")
        meta = dec.get("meta_activa", "")
        outcome = dec.get("outcome", "")
        post_obs = dec.get("post_observation", "")

        if meta.startswith("EXPLORE"):
            explore_steps += 1
            if dec.get("no_unexplored_left", False):
                explored_with_nothing_left += 1

        if act.startswith("INTERACT:") and outcome == "FAILED":
            tid = act.split(":")[1]
            info = episode_entities_catalog.get(tid, {"role": "UNKNOWN", "type": tid, "description": ""})
            failed_interactions.append((tid, info, post_obs))

    # 2. Auditoría de TIMEOUT
    if timed_out:
        scrap_entities = [
            info for info in episode_entities_catalog.values()
            if info.get("role") == "USELESS_SCRAP"
        ]
        if scrap_entities:
            added, _ = upsert_or_escalate_principle(
                mem,
                failure_signature="Q1_FALSE_SCRAP_PROTECTION",
                category="PERCEPTION_Q1",
                context_tag="ALWAYS",
                priority_tier=1,
                rule_variants=[
                    "Only classify obvious rubble, broken junk, or trash as USELESS_SCRAP; classify all containers, machines, components, and tools as useful.",
                    "CRITICAL: Treat every interactive object, container, vat, core, and portable part as useful unless its description explicitly says broken debris."
                ],
                source_mission=mission_id,
                initial_weight=1.35
            )
            if added:
                new_principles.append(f"[Q1-Safety] {added}")
            diagnostic_notes.append("Timed out after discarding entities as USELESS_SCRAP")

        if explored_with_nothing_left > 0:
            added, _ = upsert_or_escalate_principle(
                mem,
                failure_signature="Q2_EXPLORE_WHEN_MAP_EXHAUSTED",
                category="STRATEGY_Q2",
                context_tag="ALWAYS",
                priority_tier=1,
                rule_variants=[
                    "When no unexplored areas remain and devices are still uncompleted, target the intermediate workstation matching your inventory instead of exploring.",
                    "CRITICAL: Never explore when all rooms are already mapped; always operate the workstation or device that combines your collected items."
                ],
                source_mission=mission_id,
                initial_weight=1.40
            )
            if added:
                new_principles.append(f"[Q2-ExhaustedExploreFix] {added}")
            diagnostic_notes.append("Chose EXPLORE when no unexplored areas remained and devices were uncompleted")

        if failed_interactions:
            added, _ = upsert_or_escalate_principle(
                mem,
                failure_signature="Q2_EXCLUSION_AND_PREREQ_TIMEOUT",
                category="STRATEGY_Q2",
                context_tag="ALWAYS",
                priority_tier=1,
                rule_variants=[
                    "Respect negative exclusions (e.g., unloaded, without X, incompatible) and complete prerequisite workstations or switches before targeting final consoles.",
                    "CRITICAL: Never target a locked final console before crafting or activating its required key item at intermediate workstations."
                ],
                source_mission=mission_id,
                initial_weight=1.35
            )
            if added:
                new_principles.append(f"[Q2-ConstraintFix] {added}")
            diagnostic_notes.append("Timed out after failed interaction due to unmet or exclusionary condition")

        uncompleted_useful = [
            eid for eid, info in episode_entities_catalog.items()
            if info.get("role") != "USELESS_SCRAP" and eid not in completed_entity_ids
        ]
        if uncompleted_useful and explored_with_nothing_left == 0 and (total_steps == 0 or (explore_steps / max(total_steps, 1)) >= 0.30):
            added, _ = upsert_or_escalate_principle(
                mem,
                failure_signature="Q2_PREMATURE_EXPLORE_TIMEOUT",
                category="STRATEGY_Q2",
                context_tag="ALWAYS",
                priority_tier=1,
                rule_variants=[
                    "When uncompleted portable items or unlocked devices are visible, interact with them instead of exploring."
                ],
                source_mission=mission_id,
                initial_weight=1.30
            )
            if added:
                new_principles.append(f"[Q2-ExploreFix] {added}")
            diagnostic_notes.append("Kept exploring while uncompleted useful entities were visible")

    # 3. Auditoría de SESGO ÁVIDO (También aprende si hubo regresión sin regla aplicada)
    if failed_interactions and not timed_out and (not is_regression or len(episode_applied_rule_ids) == 0):
        added, _ = upsert_or_escalate_principle(
            mem,
            failure_signature="Q2_CAUSAL_ORDER_FIRST",
            category="STRATEGY_Q2",
            context_tag="HAS_LOCKED_GOALS",
            priority_tier=1,
            rule_variants=[
                "Target only the items or switches explicitly required by locked goals; ignore unrelated portable items.",
                "Only target a locked console when its exact listed requirements are satisfied; never collect unrelated distractor items."
            ],
            source_mission=mission_id,
            initial_weight=1.30
        )
        if added:
            new_principles.append(f"[Q2-CausalOrder] {added}")
        diagnostic_notes.append("Attempted locked interaction before satisfying all prerequisites")

    # 4. Auditoría de EFICIENCIA ESPACIAL (Con variante escalable)
    if success and ideal_steps > 0 and spl < 0.85 and not is_regression:
        added, _ = upsert_or_escalate_principle(
            mem,
            failure_signature="Q2_SAFE_SPATIAL_TIEBREAK",
            category="STRATEGY_Q2",
            context_tag="MULTI_TARGET",
            priority_tier=2,
            rule_variants=[
                "Among unlocked items or intermediate slots of equal causal priority, choose the closest one (lowest steps) to reduce backtracking.",
                "Use proximity (lowest steps) only to pick the nearest needed item or its matching local slot, never to approach multi-requirement consoles."
            ],
            source_mission=mission_id,
            initial_weight=1.10
        )
        if added:
            new_principles.append(f"[Q2-SpatialSPL] {added}")
        diagnostic_notes.append(f"Suboptimal path length: {total_steps} vs {ideal_steps} ideal (SPL={spl:.2f})")

    if diagnostic_notes and timed_out and not is_regression:
        llm_general_rule = abstract_failure_with_local_llm(
            timed_out, spl, diagnostic_notes, episode_entities_catalog
        )
        if llm_general_rule:
            sig = f"LLM_ABSTRACT_Q2_{len(mem['principles']) + 1}"
            added, _ = upsert_or_escalate_principle(
                mem, sig, "STRATEGY_Q2", "ALWAYS", 1, [llm_general_rule], mission_id, initial_weight=1.10
            )
            if added:
                new_principles.append(f"[LLM-Abstract] {added}")

    mem["episode_history"].append({
        "mission_id": mission_id,
        "success": success,
        "timed_out": timed_out,
        "steps": total_steps,
        "ideal_steps": ideal_steps,
        "spl": spl,
        "timestamp": datetime.now().isoformat(timespec="seconds")
    })
    if len(mem["episode_history"]) > 150:
        mem["episode_history"] = mem["episode_history"][-150:]

    save_general_memory(mem)
    return new_principles, reinforced_principles


@asynccontextmanager
async def lifespan(app: FastAPI):
    mem = load_general_memory()
    active_count = sum(1 for p in mem["principles"] if p.get("active", True))
    print(
        f"\n[Voyager V9] Motor: {DECISION_ENGINE} | Memoria Segura Cross-Task Activa "
        f"({active_count} principios activos, {len(mem['episode_history'])} episodios)."
    )
    yield


app = FastAPI(title="Voyager V9 - Safe Cross-Task Generalizable Planner", lifespan=lifespan)


@app.post("/start_benchmark")
async def start_benchmark():
    global current_test_decisions, active_target_id, active_target_type
    global known_useful_ids, known_useless_ids, completed_entity_ids, completed_states_memory
    global entity_type_semantics_cache, entity_feedback_memory, recent_observations
    global last_inventory, last_remembered_ids, last_action_taken
    global episode_entities_catalog, episode_applied_rule_ids, current_ideal_steps, current_mission_text

    current_test_decisions = []
    current_ideal_steps = 0
    current_mission_text = ""
    active_target_id = "EXPLORE"
    active_target_type = "EXPLORE"
    known_useful_ids.clear()
    known_useless_ids.clear()
    completed_entity_ids.clear()
    completed_states_memory.clear()
    episode_entities_catalog.clear()
    episode_applied_rule_ids.clear()
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
# 1. EVALUADOR DE EVENTOS ReAct
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

            if current_test_decisions:
                if len(new_items) > 0:
                    current_test_decisions[-1]["outcome"] = "ITEM_PICKED"
                    current_test_decisions[-1]["item_gained"] = new_items[0]
                else:
                    current_test_decisions[-1]["outcome"] = "SUCCESS"
                current_test_decisions[-1]["post_observation"] = obs

            for dep in deposited_items:
                entry = f"{humanize_label(dep)} stored in {humanize_label(target_id)}"
                if entry not in completed_states_memory:
                    completed_states_memory.append(entry)

            if not new_items:
                act_entry = f"{humanize_label(target_id)} activated and stored"
                if act_entry not in completed_states_memory:
                    completed_states_memory.append(act_entry)
        else:
            obs = unity_obs or f"OBSERVATION: {last_action_taken} FAILED! Missing required condition."
            entity_feedback_memory[target_id] = "FAILED"

            if current_test_decisions:
                current_test_decisions[-1]["outcome"] = "FAILED"
                current_test_decisions[-1]["post_observation"] = obs

    else:
        obs = unity_obs or f"OBSERVATION: Executed {last_action_taken}."
        if current_test_decisions:
            current_test_decisions[-1]["outcome"] = "OK"
            current_test_decisions[-1]["post_observation"] = obs
        if len(new_items) > 0 or len(deposited_items) > 0 or len(consumed_ids) > 0:
            entity_feedback_memory.clear()
            trigger_replan = True

    last_inventory = list(state.inventory)
    last_remembered_ids = current_ids
    return obs, trigger_replan


# =====================================================================
# 2. Q1: PERCEPCIÓN SEMÁNTICA CONSERVADORA
# =====================================================================
def classify_spotted_entities(state: UnifiedHudReActStateDTO) -> tuple[List[str], List[str], int, bool]:
    new_useful, new_useless = [], []
    total_tok = 0
    found_new_useful = False

    perception_principle = get_top_principle("PERCEPTION_Q1", ["ALWAYS"])
    if perception_principle:
        episode_applied_rule_ids.add(perception_principle["id"])

    for spotted in state.newly_spotted:
        if spotted.type in entity_type_semantics_cache:
            role = entity_type_semantics_cache[spotted.type]
        else:
            q1_state = {
                "mission": state.mission,
                "entity": spotted.type,
                "description": spotted.description
            }
            if perception_principle:
                q1_state["learned_guideline"] = perception_principle["rule_text"]

            q1 = {
                "role": {
                    "type": "choice",
                    "instructions": (
                        f"Analyze '{spotted.type}' ('{spotted.description}'). "
                        f"Apply learned_guideline if present. What functional role does this entity represent?"
                    ),
                    "criteria": {
                        "PICKUP_ITEM": "Any portable object, material, bar, code, keycard, key, battery, cable, fuse, circuit, cartridge, bottle, or tool that can be picked up.",
                        "ENVIRONMENT_DEVICE": "Any fixed stationary structure, door, bench, furnace, workstation, container, vat, slot, receptacle, socket, core, chest, vault, switch, valve, terminal, or console.",
                        "USELESS_SCRAP": "ONLY obvious inert trash, rubble, or broken scrap debris with zero interactive function."
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
                elif any(k in spotted.type for k in ["Door", "Server", "Slot", "Receptacle", "Switch", "Console", "Vault", "Chest", "Reactor", "Core", "Container", "Bench", "Furnace", "PC"]):
                    role = "ENVIRONMENT_DEVICE"
                else:
                    role = "PICKUP_ITEM"

            entity_type_semantics_cache[spotted.type] = role
            print(f"[Q1 Percepción V9] {spotted.id} ({spotted.type}) -> {role}")

        episode_entities_catalog[spotted.id] = {
            "type": spotted.type,
            "description": spotted.description,
            "role": role
        }

        if role != "USELESS_SCRAP":
            known_useful_ids.add(spotted.id)
            new_useful.append(spotted.id)
            found_new_useful = True
        else:
            known_useless_ids.add(spotted.id)
            new_useless.append(spotted.id)

    return new_useful, new_useless, total_tok, found_new_useful


# =====================================================================
# 3. DESCOMPOSICIÓN ATÓMICA CON AISLAMIENTO DE SUSTANTIVOS Y NEGACIONES
# =====================================================================
def extract_atomic_requirements(description: str) -> tuple[List[tuple[str, bool]], bool]:
    """
    Extrae cláusulas atómicas limpias (solo el sustantivo requerido/prohibido) y detecta
    si el dispositivo exige estados almacenados/activados previos en la sala.
    """
    desc_clean = re.split(r"\b(?:effect|efecto)\s*:", description, flags=re.IGNORECASE)[0].strip()
    clauses_with_polarity: List[tuple[str, bool]] = []
    requires_stored_state = any(
        w in desc_clean.lower()
        for w in ["stored", "activated", "almacenado", "activado", "turned on", "good terms"]
    )

    neg_patterns = [
        r"(?:without|not carrying|must not have|forbidden|incompatible with|exclude|excludes|no)\s+([^.;,]+)",
        r"(?:sin|no tener|prohibido|excluye|incompatible con)\s+([^.;,]+)"
    ]
    for pat in neg_patterns:
        for m in re.finditer(pat, desc_clean, flags=re.IGNORECASE):
            neg_item = strip_leading_articles(m.group(1).strip(" ."))
            if len(neg_item) > 1 and neg_item.lower() not in {"item", "items", "nada", "nothing"}:
                clauses_with_polarity.append((neg_item, True))

    match = re.search(
        r"(?:requires?|rquires?|requiere|needs?|uses?|combines?|only if|if|solo si|with a|with|stores?|for)\s*:?\s*([^.]+)",
        desc_clean,
        flags=re.IGNORECASE
    )
    if match:
        req_text = match.group(1).strip()
        req_text = re.split(
            r"\b(?:without|not carrying|must not|forbidden|incompatible|exclude|sin|prohibido)\b",
            req_text,
            flags=re.IGNORECASE
        )[0].strip()

        # Eliminamos sufijos verbales de estado compartido para que cada cláusula conserve solo el sustantivo
        suffix_match = re.search(
            r"\b(?:must be stored|must be activated|must be active|are in good terms|in good terms|stored|activated|almacenados|activados)\s*$",
            req_text,
            flags=re.IGNORECASE
        )
        if suffix_match:
            requires_stored_state = True
            req_text = req_text[:suffix_match.start()].strip()

        raw_clauses = re.split(r",|\band\b|\by\b|&", req_text, flags=re.IGNORECASE)
        for c in raw_clauses:
            c_clean = strip_leading_articles(c.strip(" ."))
            if len(c_clean) > 1:
                clauses_with_polarity.append((c_clean, False))

    return clauses_with_polarity, requires_stored_state


def get_actionable_entities_agnostic(
    entities: List[EntityHudDTO],
    inventory: List[str]
) -> tuple[List[EntityHudDTO], List[EntityHudDTO], int]:
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
            requires_cond = any(w in desc_l for w in [
                "require", "requires", "rquire", "rquires", "requiere",
                "need", "needs", "use", "uses", "combine", "combines",
                "must be", "only if", "solo si", "unlock with", "locked",
                "slot", "store", "insert", "without", "forbidden", "incompatible", "exclude", "sin", "prohibido"
            ])
            says_none = any(w in desc_l for w in ["requires: none", "no item", "requiere: nada", "requires: nothing"])
            if not requires_cond or says_none:
                actionable.append(e)
            else:
                devices_to_verify.append(e)

    if not devices_to_verify:
        return actionable, locked_devices, 0

    clean_inventory = [humanize_label(i) for i in inventory]
    clean_stored = [humanize_label(s) for s in completed_states_memory]
    combined_items = clean_inventory + clean_stored

    held_only_str = ", ".join(clean_inventory) if clean_inventory else "Empty"
    stored_only_str = ", ".join(clean_stored) if clean_stored else "Empty"
    combined_str = ", ".join(combined_items) if combined_items else "Empty"

    eval_state = {
        "held_inventory": held_only_str,
        "stored_and_activated_states": stored_only_str,
        "all_current_progress": combined_str
    }

    questions = {}
    device_clauses_map: Dict[str, List[tuple[str, bool]]] = {}

    for d in devices_to_verify:
        clauses, needs_stored_state = extract_atomic_requirements(d.description)
        device_clauses_map[d.id] = clauses

        if not clauses:
            actionable.append(d)
            continue

        has_positive = any(not is_neg for _, is_neg in clauses)
        if has_positive and combined_str == "Empty":
            device_clauses_map[d.id] = []
            locked_devices.append(d)
            continue

        target_pool_str = stored_only_str if needs_stored_state else combined_str

        if has_positive and target_pool_str == "Empty":
            device_clauses_map[d.id] = []
            locked_devices.append(d)
            continue

        for idx, (clause, is_negative) in enumerate(clauses):
            q_key = f"can_use_{d.id}_{idx}"
            if is_negative:
                questions[q_key] = {
                    "type": "choice",
                    "instructions": (
                        f"Negative exclusion check for '{d.type}': '{clause}' is FORBIDDEN in held_inventory [{held_only_str}]. "
                        f"Is held_inventory [{held_only_str}] free of '{clause}'?"
                    ),
                    "criteria": {
                        "READY": f"YES. '{clause}' is NOT present in [{held_only_str}].",
                        "NOT_READY": f"NO. '{clause}' IS present in [{held_only_str}], violating the exclusion."
                    }
                }
            else:
                questions[q_key] = {
                    "type": "choice",
                    "instructions": (
                        f"Compare [{target_pool_str}] with the required item/entity '{clause}' for '{d.type}'. "
                        f"Is '{clause}' explicitly present in [{target_pool_str}]?"
                    ),
                    "criteria": {
                        "READY": f"YES. '{clause}' is explicitly present in [{target_pool_str}].",
                        "NOT_READY": f"NO. '{clause}' is missing from [{target_pool_str}]."
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
# 4. Q2 ESTRATÉGICO (ORDEN NATURAL DE DESCUBRIMIENTO + AHORRO DE TOKENS)
# =====================================================================
def update_strategic_goal_react(
    state: UnifiedHudReActStateDTO,
    actionable_entities: List[EntityHudDTO],
    locked_devices: List[EntityHudDTO],
    can_still_explore: bool
) -> tuple[str, str, float, int, Optional[str]]:
    candidate_entities = list(actionable_entities)
    exhausted_exploration_fallback = False

    if not candidate_entities:
        unfailed_locked = [d for d in locked_devices if d.id not in entity_feedback_memory]

        if can_still_explore or not unfailed_locked:
            return "EXPLORE", "EXPLORE", 1.0, 0, None

        candidate_entities = unfailed_locked
        exhausted_exploration_fallback = True
        print(f"[Q2 Recovery V9] Mapa explorado. Evaluando dispositivos pendientes no fallidos: {[d.id for d in candidate_entities]}")

    # Solo consultamos principios de Q2 cuando hay al menos 2 opciones que desambiguar
    strategy_principle = None
    applied_rule_text = None
    use_spatial_tiebreak = False

    if len(candidate_entities) >= 2:
        context_tags = ["ALWAYS"]
        if len(locked_devices) >= 1 and not exhausted_exploration_fallback:
            context_tags.append("HAS_LOCKED_GOALS")
        else:
            context_tags.append("MULTI_TARGET")

        strategy_principle = get_top_principle("STRATEGY_Q2", context_tags)
        if strategy_principle:
            episode_applied_rule_ids.add(strategy_principle["id"])
            applied_rule_text = strategy_principle["rule_text"]
            use_spatial_tiebreak = (strategy_principle.get("failure_signature") == "Q2_SAFE_SPATIAL_TIEBREAK")

    inv_str = ", ".join([humanize_label(i) for i in state.inventory]) if state.inventory else "Empty"
    remaining_locked = [d for d in locked_devices if d not in candidate_entities]
    locked_str = (
        "\n".join([f"- {d.id} ({d.type}): {d.description}" for d in remaining_locked])
        if remaining_locked
        else "None"
    )

    eval_state_q2 = {
        "mission": state.mission,
        "locked_goals_to_unlock": locked_str,
        "current_inventory": inv_str
    }
    if exhausted_exploration_fallback:
        eval_state_q2["exploration_status"] = "All areas explored. Select the workstation/device matching current_inventory."
    if applied_rule_text:
        eval_state_q2["general_strategy_rule"] = applied_rule_text

    # Mantenemos el orden natural de descubrimiento salvo cuando la heurística espacial está activa
    ordered_candidates = (
        sorted(candidate_entities, key=lambda x: x.steps)
        if use_spatial_tiebreak
        else candidate_entities
    )

    criteria = {}
    for e in ordered_candidates:
        if use_spatial_tiebreak:
            criteria[e.id] = f"Interact with {e.type} ({e.id}) [{e.steps} steps away]: {e.description}"
        else:
            criteria[e.id] = f"Interact with {e.type} ({e.id}): {e.description}"

    q2 = {
        "decision": {
            "type": "choice",
            "instructions": (
                f"Mission: '{state.mission}'. Locked goals requirements: [{locked_str}]. "
                f"Evaluate options with current_inventory [{inv_str}]"
                + (" and general_strategy_rule" if applied_rule_text else "")
                + ". Which entity do you target to progress in the mission?"
            ),
            "criteria": criteria
        }
    }

    try:
        ans, tok = run_inference(eval_state_q2, q2)
        choice = ans.get("decision", {}).get("choice", ordered_candidates[0].id)
        conf = ans.get("decision", {}).get("confidence", 0.0)

        target_ent = next((e for e in ordered_candidates if e.id == choice), ordered_candidates[0])
        return target_ent.id, target_ent.type, conf, tok, applied_rule_text
    except Exception as e:
        print(f"[Q2 Error] {e}")
        return ordered_candidates[0].id, ordered_candidates[0].type, 1.0, 0, applied_rule_text


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
# 6. ENDPOINT PRINCIPAL DE PLANIFICACIÓN V9
# =====================================================================
@app.post("/plan_action")
async def plan_action(state: UnifiedHudReActStateDTO):
    global current_mission_id, current_mission_text, current_ideal_steps
    global active_target_id, active_target_type, last_action_taken, recent_observations

    start_time = time.time()
    current_mission_id = state.mission_id or "Mision_V9_Generalizable"
    if state.mission:
        current_mission_text = state.mission
    if state.ideal_steps > 0:
        current_ideal_steps = state.ideal_steps

    for ent in state.remembered_entities:
        if ent.id not in episode_entities_catalog:
            role = entity_type_semantics_cache.get(ent.type, "PICKUP_ITEM")
            episode_entities_catalog[ent.id] = {
                "type": ent.type,
                "description": ent.description,
                "role": role
            }

    # 1. Q1: Percepción semántica conservadora
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
    can_still_explore = has_unexplored_environment(state.available_actions)
    unfailed_active_ids = {eid for eid in active_ids_in_world if eid not in entity_feedback_memory}

    # 3. Disparador de replanificación
    should_run_q2 = (
        trigger_replan
        or spotted_new_useful
        or (active_target_id != "EXPLORE" and active_target_id not in active_ids_in_world)
        or (active_target_id in entity_feedback_memory)
        or (active_target_id == "EXPLORE" and not can_still_explore and len(unfailed_active_ids) > 0)
    )

    q2_tok = 0
    q2_conf = 1.0
    req_tok = 0
    applied_rule = None

    if should_run_q2:
        actionable_entities, locked_devices, req_tok = get_actionable_entities_agnostic(
            state.remembered_entities,
            state.inventory
        )
        active_target_id, active_target_type, q2_conf, q2_tok, applied_rule = update_strategic_goal_react(
            state,
            actionable_entities,
            locked_devices,
            can_still_explore=can_still_explore
        )
        if applied_rule:
            print(f"[General Principle Applied V9] {applied_rule}")
        print(f"[ReAct Meta V9] Obs: '{latest_obs}' -> Meta: {active_target_id} ({active_target_type}) [Conf: {q2_conf:.2f}]")

    # 4. Disparo directo en mira si alcanzamos físicamente la meta
    if active_target_id != "EXPLORE":
        interact_code = f"INTERACT:{active_target_id}"
        if any(a.code == interact_code for a in state.available_actions):
            lat = round(time.time() - start_time, 2)
            total_tok = q1_tok + req_tok + q2_tok
            last_action_taken = interact_code
            thought = f"[ReAct-V9|{total_tok}t] Target Reached: {active_target_id} => {interact_code}"
            current_test_decisions.append({
                "observacion_previa": latest_obs,
                "post_observation": "",
                "outcome": "PENDING",
                "item_gained": "",
                "no_unexplored_left": not can_still_explore,
                "meta_activa": f"{active_target_id}({active_target_type})",
                "principio_general_usado": applied_rule or "NONE",
                "inventario_snapshot": list(state.inventory),
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
        thought = f"[ReAct-V9-Auto|{total_tok}t] Goal: {active_target_id} => {chosen_code}"
        current_test_decisions.append({
            "observacion_previa": latest_obs,
            "post_observation": "",
            "outcome": "PENDING",
            "item_gained": "",
            "no_unexplored_left": not can_still_explore,
            "meta_activa": f"{active_target_id}({active_target_type})",
            "principio_general_usado": applied_rule or "NONE",
            "inventario_snapshot": list(state.inventory),
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
        f"[ReAct-V9|{total_tok}t] Obs: '{latest_obs}' | "
        f"Goal: {active_target_id}({active_target_type}) -> Act: {chosen_code} (Conf: {q3_conf:.2f})"
    )

    current_test_decisions.append({
        "observacion_previa": latest_obs,
        "post_observation": "",
        "outcome": "PENDING",
        "item_gained": "",
        "no_unexplored_left": not can_still_explore,
        "meta_activa": f"{active_target_id}({active_target_type})",
        "principio_general_usado": applied_rule or "NONE",
        "inventario_snapshot": list(state.inventory),
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
    global current_test_decisions, current_mission_id, current_mission_text, current_ideal_steps

    filename = "./versiones/Voyager_Architecture_v9_Generalizable.json"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    latencias = [d["latencia"] for d in current_test_decisions]
    tokens_list = [d.get("tokens_entrada", 0) for d in current_test_decisions]

    pasos_totales = len(current_test_decisions) if current_test_decisions else data.pasos_unity
    ideal_steps = data.ideal_steps if data.ideal_steps > 0 else current_ideal_steps
    exito = not data.timed_out

    if exito and current_test_decisions and current_test_decisions[-1]["decision"].startswith("INTERACT:"):
        current_test_decisions[-1]["outcome"] = "SUCCESS"
        current_test_decisions[-1]["post_observation"] = "OBSERVATION: Final mission goal completed."

    if not exito:
        spl = 0.0
    elif ideal_steps > 0:
        spl = round(ideal_steps / max(pasos_totales, ideal_steps), 4)
    else:
        spl = 1.0

    new_principles, reinforced_principles = distill_general_experience(
        mission_id=current_mission_id,
        mission_text=current_mission_text,
        decisions=current_test_decisions,
        success=exito,
        timed_out=data.timed_out,
        total_steps=pasos_totales,
        ideal_steps=ideal_steps,
        spl=spl
    )

    print(
        f"\n[Benchmark V9 Finalizado] Misión: {current_mission_id} | Éxito: {exito} | "
        f"Pasos: {pasos_totales} vs Ideal: {ideal_steps} | SPL: {spl:.4f} | "
        f"Nuevos principios: {len(new_principles)} | Reforzados: {len(reinforced_principles)}"
    )
    for p_txt in new_principles:
        print(f"   + [NUEVO] {p_txt}")

    new_test = {
        "mision": current_mission_id,
        "exito": exito,
        "duracion_total": round(data.duracion_total, 2),
        "supero_limite_tiempo": data.timed_out,
        "pasos_totales": pasos_totales,
        "pasos_ideales": ideal_steps,
        "spl": spl,
        "principios_aprendidos_en_episodio": new_principles,
        "principios_reforzados_en_episodio": reinforced_principles,
        "tokens_promedio_por_paso": round(statistics.mean(tokens_list), 2) if tokens_list else 0,
        "latencia_promedio": round(statistics.mean(latencias), 4) if latencias else 0.0,
        "decisiones": current_test_decisions
    }

    full_data = []
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                full_data = json.load(f)
        except Exception:
            pass

    model_entry = next((item for item in full_data if item["modelo"] == ARCHITECTURE_TAG), None)
    if not model_entry:
        model_entry = {
            "modelo": ARCHITECTURE_TAG,
            "pruebas": [],
            "total_timeouts": 0,
            "spl_promedio": 0.0
        }
        full_data.append(model_entry)

    model_entry["pruebas"].append(new_test)
    model_entry["total_timeouts"] = sum(1 for p in model_entry["pruebas"] if p.get("supero_limite_tiempo", False))
    spl_values = [p["spl"] for p in model_entry["pruebas"] if "spl" in p]
    model_entry["spl_promedio"] = round(statistics.mean(spl_values), 4) if spl_values else 0.0

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(full_data, f, indent=4, ensure_ascii=False)

    await start_benchmark()
    return {
        "status": "saved",
        "architecture": ARCHITECTURE_TAG,
        "exito": exito,
        "pasos_totales": pasos_totales,
        "pasos_ideales": ideal_steps,
        "spl": spl,
        "new_principles_count": len(new_principles),
        "reinforced_principles_count": len(reinforced_principles),
        "new_principles": new_principles
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)