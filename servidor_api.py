import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn
from laya import Router

app = FastAPI(title="IAM-3D Laya Brain")
print("Precargando modelo Laya en memoria...")
router = Router(preload=True, device="cpu") 

# Estructura optimizada para evitar problemas de serialización en Unity
class AgentState(BaseModel):
    current_node: str
    option_nodes: List[str]
    option_descriptions: List[str]

@app.post("/decide_next_move")
async def decide_next_move(state_data: AgentState):
    start_time = time.time()
    
    state = {
        "current_location": state_data.current_node,
        "mission": "Explore the maze. Always prioritize UNEXPLORED paths. Only retreat if necessary."
    }

    # Reconstruimos el diccionario a partir de las listas enviadas por Unity
    criteria_dict = dict(zip(state_data.option_nodes, state_data.option_descriptions))

    questions = {
        "next_node": {
            "type": "choice",
            "instructions": "Select the best next node based on the descriptions.",
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
    print("Iniciando API del Agente Laya en http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)