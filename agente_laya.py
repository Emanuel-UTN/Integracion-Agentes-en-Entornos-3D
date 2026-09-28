import time
from laya import Router

print("Cargando Laya AI en memoria...")
router = Router(preload=True, device="cpu") 

maze_graph = {
    "Node_A": ["Node_B"],
    "Node_B": ["Node_A", "Node_C"],
    "Node_C": ["Node_B", "Node_D"],
    "Node_D": ["Node_C", "Node_F"],
    "Node_E": [], # Salida
    "Node_F": ["Node_D", "Node_G", "Node_H"],
    "Node_G": ["Node_F"],
    "Node_H": ["Node_F", "Node_E"]
}

current_node = "Node_A"
visited_nodes = ["Node_A"]
is_goal_reached = False
steps = 0

print("\nStarting Graph Navigation Agent with Laya (English)...")

while not is_goal_reached and steps < 15:
    steps += 1
    print(f"\n--- Step {steps} ---")

    accessible_nodes = maze_graph[current_node]

    # Estado con tamaño constante, sin pasar el historial completo
    state = {
        "current_location": current_node,
        "mission": "Explore to find Node_E. Always prioritize unexplored nodes. Only select an already visited node if you hit a dead end."
    }

    # Procesamiento algorítmico previo a la inferencia
    criteria_dict = {}
    for node in accessible_nodes:
        if node in visited_nodes:
            criteria_dict[node] = f"Move BACK to {node} (WARNING: Already visited, avoid unless dead end)"
        else:
            criteria_dict[node] = f"Explore NEW node {node} (PRIORITY)"

    questions = {
        "next_node": {
            "type": "choice",
            "instructions": "Evaluate the options and select the mandatory next node.",
            "criteria": criteria_dict
        }
    }

    start_time = time.time()
    result = router.predict(state, questions)
    end_time = time.time()

    chosen_node = result['answers']['next_node']['choice']
    print(f"[Laya Decision] -> Move to {chosen_node} (Latency: {end_time - start_time:.2f} seconds)")

    # Actualizar la física/entorno y la memoria
    current_node = chosen_node
    if current_node not in visited_nodes:
        visited_nodes.append(current_node)

    if current_node == "Node_E":
        is_goal_reached = True
        print("[Environment] -> SUCCESS: Reached Exit Node_E. Maze solved!")