import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn
from laya import Router

app = FastAPI(title="IAM-3D Semantic Brain")
print("Precargando modelo Laya en memoria...")
router = Router(preload=True, device="cpu") 

class AgentState(BaseModel):
    current_node: str
    inventory: List[str]            # Objetos recolectados (ej: ["DataModule"])
    known_objects: List[str]        # Memoria espacial (ej: ["Node_5_5: MainServer"])
    option_nodes: List[str]
    option_descriptions: List[str]

@app.post("/decide_next_move")
async def decide_next_move(state_data: AgentState):
    start_time = time.time()
    
    has_data_module = "DataModule" in state_data.inventory
    knows_server_location = any("MainServer" in obj for obj in state_data.known_objects)

    # Reglas estrictas para el prompt del Sistema 1
    if not has_data_module:
        mission_text = "OBJECTIVE: Find the 'DataModule'. RULES: 1. If you see 'CRITICAL TARGET VISIBLE', pick it immediately. 2. Avoid paths with the 'MainServer'. 3. Explore clear paths."
    elif has_data_module and not knows_server_location:
        mission_text = "OBJECTIVE: Find the 'MainServer'. RULES: 1. If you see 'FINAL TARGET VISIBLE', pick it immediately. 2. Explore clear paths."
    else:
        mission_text = "OBJECTIVE: Return to the 'MainServer'. RULES: 1. Prioritize 'SHORTEST ROUTE TO MAINSERVER'. 2. If you see 'FINAL TARGET VISIBLE', pick it immediately."

    state = {
        "current_location": state_data.current_node,
        "mission": mission_text,
        "memory_status": f"Inventory: {state_data.inventory} | Known: {state_data.known_objects}"
    }

    criteria_dict = dict(zip(state_data.option_nodes, state_data.option_descriptions))

    questions = {
        "next_node": {
            "type": "choice",
            "instructions": "Evaluate the option descriptions and select the mandatory next node that best follows your current RULES.",
            "criteria": criteria_dict
        }
    }

    try:
        result = router.predict(state, questions)
        chosen_node = result['answers']['next_node']['choice']
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    end_time = time.time()
    
    return {
        "decision": chosen_node,
        "latency_seconds": round(end_time - start_time, 2),
        "status": "success"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)