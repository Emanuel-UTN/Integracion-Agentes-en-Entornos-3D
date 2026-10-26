# Voyager Architecture v6.4: Agnostic Event-Driven ReAct Framework

**Estado:** Estable / Misión Completa Validada  
**Motor Cognitivo:** `kev-0.8b` (System One / TypeSafe API vía CPU)  
**Entorno de Ejecución:** Unity 3D (Control Egocéntrico Discreto 2.0 m / 90°)  
**Hardware de Prueba:** Intel Core i5-11400 @ 2.60GHz (6 núcleos / 12 hilos), 16 GB RAM DDR4, sin GPU dedicada (Inferencia 100% sobre CPU) 

---

## 1. Descripción General de la Arquitectura

La versión **Voyager v6.4** consolida la transición desde planificadores asistidos hacia un **agente puramente autónomo y agnóstico al dominio**. Implementa un esquema estructurado tipo *Reasoning + Acting* (**ReAct**) desacoplado en tres capas lógicas e impulsado estrictamente por eventos físicos del entorno.

El propósito central de esta versión es resolver misiones secuenciales complejas (recoger llave $\rightarrow$ abrir puerta $\rightarrow$ recoger módulo de datos $\rightarrow$ activar servidor) utilizando exclusivamente un modelo ultraliviano de 800 millones de parámetros (`kev-0.8b`), ejecutado en CPU sin aceleración gráfica, manteniendo la toma de decisiones 100% en manos de la IA y sin introducir heurísticas o nombres de objetos específicos (*cero hardcoding*) en el código fuente.

### Principios Fundamentales de Diseño

1. **Autonomía Cognitiva Absoluta:** El código en Python no toma decisiones por el modelo ni contiene reglas fijas con nombres de objetos específicos. Términos como `"Keycard"`, `"DataModule"`, `"LockedDoor"` o `"MainServer"` no existen en la lógica condicional del backend. Todas las deducciones de relevancia, requisitos y prioridades son inferidas por `kev-0.8b`.
2. **Desacoplamiento Sensoriomotor:**
   * **Unity C# (Sistema Periférico/Motor):** Mapea el espacio geométrico continuo, proyecta conos de visión con oclusión física (raycasting) y calcula trayectorias topológicas inmediatas (BFS egocéntrico).
   * **Python FastAPI (Sistema Nervioso Central):** Administra la memoria episódica, gestiona la memoria de frustración estricta y orquesta las consultas al modelo de decisión.
3. **Planificación Dirigida por Eventos (*Event-Driven Gating*):** Las consultas de alto nivel estratégico no se ejecutan en cada baldosa de desplazamiento, sino únicamente ante cambios de estado discretos (recogida de ítems, apertura de accesos, fallos de interacción o descubrimiento de nuevas entidades útiles).
4. **Caché Semántico por Tipo:** Las propiedades intrínsecas de las entidades se clasifican una sola vez por tipo y se conservan en memoria, reduciendo el consumo de inferencia a 0 ms para objetos repetidos.
5. **Ontología Funcional Universal:** En lugar de saturar al modelo con comprobaciones condicionales complejas, el entorno categoriza los objetos bajo roles universales (`PICKUP_ITEM`, `ENVIRONMENT_DEVICE`, `USELESS_SCRAP`), eliminando la colisión léxica en modelos sub-1B.

---

## 2. Flujo Cognitivo-Motor del Agente

El ciclo de ejecución en cada paso sigue la siguiente secuencia:

```
[Visión Unity (90° FOV / Raycast)] ──> ¿Hay entidades nuevas?
                                                │
                     ┌──────────────────────────┴──────────────────────────┐
                     ▼ SÍ                                                  ▼ NO
          [Q1: Percepción Semántica]                              [ReAct Feedback Evaluator]
          (Clasificación de rol ontológico)                       ¿Ocurrió un evento físico real?
                     │                                                     │
                     └──────────────────────────┬──────────────────────────┘
                                                │
                                   ┌────────────┴────────────┐
                                   ▼ SÍ                      ▼ NO
                         [Verificación Batch]       [Mantiene Meta Activa]
                         ¿Qué dispositivos están              │
                          listos con el inventario?           │
                                   │                          │
                                   ▼                          │
                          [Q2: Meta Estratégica]              │
                        (Elige meta ejecutable)               │
                                   │                          │
                                   └────────────┬─────────────┘
                                                │
                              ¿Estamos frente a la meta (0 pasos)?
                                                │
                     ┌──────────────────────────┴──────────────────────────┐
                     ▼ SÍ                                                  ▼ NO
           [Disparo Directo en Mira]                             [Q3: Navegación Motora]
           `INTERACT:{target_id}`                                 (Poda de opciones hacia meta)
                     │                                                     │
                     └──────────────────────────┬──────────────────────────┘
                                                ▼
                                [Ejecución Cinemática en Unity]
```

---

## 3. Desglose de Consultas al Modelo (`kev-0.8b`)

Todas las interacciones se realizan contra el endpoint `/v1/systemone` mediante payloads JSON tipados. Cada consulta recibe un contexto (`state`) y un objeto de preguntas estructuradas (`questions`).

### 3.1. Q1: Percepción Semántica Universal (Ontología Funcional)

* **Objetivo:** Clasificar el rol de una entidad desconocida apenas entra al cono de visión del agente.
* **Frecuencia:** Se ejecuta **una sola vez por tipo de entidad** (se almacena en `entity_type_semantics_cache`).
* **Contexto enviado (`state`):**
  ```json
  {
    "entity": "Keycard",
    "description": "A security access card. Requires: NONE. Effect: Adds Keycard to inventory."
  }
  ```
* **Estructura de la pregunta:**
  * **Nombre:** `role`
  * **Instrucción:** `"Analyze '{spotted.type}' with description '{spotted.description}'. What functional role does this entity represent?"`
  * **Criterios de evaluación:**
    * `PICKUP_ITEM`: `"A loose portable item lying on the ground (such as a keycard, cartridge, data drive, or tool) that can be picked up into inventory."`
    * `ENVIRONMENT_DEVICE`: `"A fixed stationary structure, door, barrier, machine, terminal, or console in the room that can be operated or unlocked."`
    * `USELESS_SCRAP`: `"Inert scrap metal, rubble, junk, or debris that has no practical function."`

---

### 3.2. Q-Precondición: Verificación de Dispositivos en Lote (*Batch*)

* **Objetivo:** Determinar qué dispositivos fijos (`ENVIRONMENT_DEVICE`) pueden ser operados físicamente con el inventario que el agente lleva en sus manos.
* **Frecuencia:** Solo ante eventos de inventario o cuando se descubre un dispositivo nuevo. Los objetos `PICKUP_ITEM` se consideran automáticamente viables (se levantan con las manos vacías) y no gastan tokens en esta fase.
* **Contexto enviado (`state`):**
  ```json
  {
    "held_inventory": "Keycard",
    "target_devices": "- Locked_door_01 (LockedDoor): Security door. Requires: Keycard. Effect: Opens passage.\n- Main_server (MainServer): Terminal console. Requires: DataModule. Effect: Completes mission upload."
  }
  ```
* **Estructura de la pregunta (por cada dispositivo `d` a verificar):**
  * **Nombre:** `can_use_{d.id}`
  * **Instrucción:** `"Compare held_inventory [{held_inventory}] with the requirement of '{d.type}' ({d.description}). Does the agent currently hold the item required to use '{d.type}'?"`
  * **Criterios de evaluación:**
    * `READY`: `"YES. The required item described for {d.type} is present in held_inventory [{held_inventory}]."`
    * `NOT_READY`: `"NO. The required item described for {d.type} is missing from held_inventory [{held_inventory}]."`

---

### 3.3. Q2: Selección de Meta Estratégica

* **Objetivo:** Elegir cuál de las entidades viables y operables representa la máxima prioridad para progresar en la misión.
* **Frecuencia:** Exclusiva ante eventos.
* **Bypass de Optimización:** Si la lista de entidades accionables contiene **un único elemento**, el código fija la meta de inmediato con confianza `1.00`, consumiendo 0 tokens y 0 ms de inferencia.
* **Contexto enviado (`state`):**
  ```json
  {
    "mission": "Find Keycard to open LockedDoor, collect DataModule, and upload it to MainServer.",
    "current_inventory": "Keycard",
    "ready_targets": "- Locked_door_01 (LockedDoor): Security door. Requires: Keycard. Effect: Opens passage."
  }
  ```
* **Estructura de la pregunta:**
  * **Nombre:** `decision`
  * **Instrucción:** `"All listed ready_targets are accessible right now with your current_inventory [{current_inventory}]. Which entity do you target to progress in the mission?"`
  * **Criterios de evaluación:**
    * `EXPLORE`: `"Explore to find new paths or items."`
    * `{entity.id}`: `"Interact with {entity.type} ({entity.id}): {entity.description}"` (repetido para cada entidad validada como lista).

---

### 3.4. Q3: Navegación Motora Egocéntrica (Reflejo Motor)

* **Objetivo:** Traducir la meta abstracta fijada por Q2 (`active_target_type`) en una acción cinemática inmediata sobre el cuerpo del agente.
* **Frecuencia:** En cada paso motor, siempre que existan 2 o más direcciones transitables no bloqueadas por colisionadores.
* **Contexto enviado (`state`):**
  ```json
  {
    "current_target": "LockedDoor"
  }
  ```
* **Estructura de la pregunta:**
  * **Nombre:** `step`
  * **Instrucción:**
    * Si la meta es una entidad: `"Select the action that leads directly to target LockedDoor."`
    * Si la meta es `EXPLORE`: `"Select the action that leads to an Unexplored area or Scan."`
  * **Criterios de evaluación (tras la poda de sensores):**
    ```json
    {
      "MOVE_FORWARD": "Leads directly to target LockedDoor (2 steps)",
      "TURN_LEFT": "Does not lead to current target",
      "TURN_RIGHT": "Does not lead to current target"
    }
    ```

---

## 4. Ayudas Algorítmicas y Scaffolding Proporcionado por el Código

Para que un modelo compacto de 0.8B pueda operar en tiempo real sin alucinar trayectorias ni quedar atrapado en laberintos geométricos, el entorno y el servidor actúan como un **sistema nervioso periférico** que procesa la geometría continua y le entrega al modelo opciones semánticamente pre-digeridas.

### 4.1. En Unity (C# - `EgocentricVoyagerAgent_v6.cs`)

1. **Visión por Cono y Oclusión (`FOV Raycasting`):**
   * El agente posee un campo de visión horizontal de 110° y 24 metros de alcance.
   * Mediante `Physics.RaycastAll` contra la capa de muros (`wallLayer`), se verifica si hay línea de visión directa antes de reportar un objeto a Python, impidiendo que el agente "vea a través de paredes".
2. **Topología BFS Egocéntrica:**
   * Un algoritmo de búsqueda en anchura (**BFS**) calcula en tiempo real la distancia en baldosas hacia cada entidad recordada y hacia celdas no exploradas.
   * Traduce la posición global en direcciones relativas al agente: `"CENTER (MOVE_FORWARD)"`, `"LEFT (TURN_LEFT)"`, `"RIGHT (TURN_RIGHT)"` o `"BEHIND (TURN_AROUND)"`.
3. **Mapeo de Adyacencia e Interacción en Mira (0 Pasos / Crosshair):**
   * El método `CanInteractFromCell` verifica si desde la celda actual el objeto está a alcance físico ($d \le 2.4\text{ m}$) y alineado cardinalmente a menos de 40° de la orientación frontal del agente.
   * Si el agente gira y queda alineado frontalmente, Unity activa la opción física inmediata: `INTERACT:{entityId}` con estado `"CROSSHAIR"`.
4. **Muestreo de Exploración (*Cell Inspection*):**
   * Al recorrer el mapa, las celdas vistas dentro del cono se marcan en `inspectedCells`.
   * El sensor describe a Python las direcciones inexploradas con métricas concretas: `"Unexplored area (4 steps)"` o `"Scan 5 unseen cells"`.

### 4.2. En Python (FastAPI - `server_voyager_v6.py`)

1. **Planificación Dirigida por Eventos (*Event-Driven Gating*):**
   * El servidor compara `inventory` y `remembered_entities` contra el estado anterior.
   * Si el agente simplemente avanzó o giró sin descubrir objetos nuevos, **Q2 no se ejecuta**. Esto ahorra más del 70% del cómputo en CPU por misión.
2. **Caché Semántico por Tipo (`entity_type_semantics_cache`):**
   * Se almacena el rol ontológico de cada tipo de entidad (`Keycard` $\rightarrow$ `PICKUP_ITEM`, `LockedDoor` $\rightarrow$ `ENVIRONMENT_DEVICE`). Si en el escenario existen múltiples puertas o módulos, el costo de inferencia perceptual para las instancias subsiguientes es de **0 ms**.
3. **Disparo Directo en Mira (0.00s Latency Bypass):**
   * Si la meta fijada por Q2 es `Keycard` y en las acciones disponibles de Unity aparece `INTERACT:Keycard`, el servidor despacha la interacción de inmediato sin consultar a Q3.
4. **Poda de Ruido Sensorial para Q3 (`build_pruned_q3_criteria`):**
   * Antes de consultar el motor de movimiento, el servidor filtra las cadenas de Unity mediante expresiones regulares.
   * Si la meta es `LockedDoor`, solo el comando que conduce hacia ella conserva la pista; las direcciones irrelevantes se enmascaran como `"Does not lead to current target"`, evitando distracciones en el cabezal de atención del modelo de 0.8B.
5. **Memoria de Frustración Estricta (Anti-Bucle):**
   * Si el agente interactúa con una entidad y la acción fracasa, esta se registra en `entity_feedback_memory[target_id] = "FAILED"`.
   * **Regla de oro:** Esta memoria de frustración **únicamente se limpia si entra un nuevo ítem físico al inventario**. Abrir una puerta o caminar no borra el fallo, garantizando que el agente no vuelva a intentar activar un servidor sin poseer el módulo requerido.
6. **Registro de Entidades Completadas (`completed_entity_ids`):**
   * Al desbloquear con éxito una puerta o recoger un ítem, el identificador de la entidad entra en `completed_entity_ids`, excluyéndola de futuras decisiones estratégicas para que el agente no intente interactuar dos veces con un objetivo resuelto.

---

## 5. Resumen de Flujo en Ejecución (Caso de Estudio)

A continuación se detalla la traza completa de ejecución determinista validada en la misión de integración:

| Paso | Evento Físico en Unity | Inventario | Módulos Invocados | Meta Elegida | Acción Física Resultante |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | Spawn inicial | `[]` | Q2 (0 viables) | `EXPLORE` (1.00) | `TURN_AROUND` (busca área inexplorada) |
| **2** | Avista `Locked_door_01` | `[]` | Q1 (`LockedDoor` $\rightarrow$ Device) | `EXPLORE` (1.00) | `TURN_RIGHT` (continúa explorando) |
| **3** | Avista `Keycard` y `Server` | `[]` | Q1 (`Keycard` $\rightarrow$ Item, `Server` $\rightarrow$ Device) | **`Keycard`** (1.00) | `MOVE_FORWARD` (navega a Keycard) |
| **4** | Frente a la Keycard (0 pasos) | `[]` | Disparo en Mira directo (0.00s) | `Keycard` | `INTERACT:Keycard` $\rightarrow$ **SUCCESS** |
| **5** | Inventario actualizado | `[Keycard]` | Q-Precondición + Q2 | **`Locked_door_01`** (1.00) | Navega hacia la puerta cerrada |
| **6** | Frente a la puerta (0 pasos) | `[Keycard]` | Disparo en Mira directo (0.00s) | `Locked_door_01` | `INTERACT:Locked_door_01` $\rightarrow$ **SUCCESS** |
| **7** | Puerta abierta y consumida | `[Keycard]` | Q2 (Server requiere DataModule) | `EXPLORE` (1.00) | Cruza el umbral hacia la nueva sala |
| **8** | Avista `Data_Module_01` | `[Keycard]` | Q1 (`DataModule` $\rightarrow$ Item) | **`Data_Module_01`** (1.00) | Navega hacia el módulo de datos |
| **9** | Recoge módulo de datos | `[Keycard, DataModule]` | Q-Precondición (Server $\rightarrow$ `READY`) | **`Main_server`** (1.00) | Navega de regreso al servidor |
| **10**| Frente a la consola central | `[Keycard, DataModule]` | Disparo en Mira directo (0.00s) | `Main_server` | `INTERACT:Main_server` $\rightarrow$ **MISIÓN CUMPLIDA** |

---

## 6. Rendimiento Experimental y Métricas de Benchmark

Resultados consolidados a lo largo de 10 ejecuciones completas en la misión estándar (`Keycard -> Door -> DataModule -> Server`):

### 6.1. Métricas Globales de EjecuciónProcesador de prueba:
* Intel Core i5-11400 @ 2.60GHz (12 hilos, 16 GB RAM DDR4, sin GPU)
* Tasa de Éxito: 100% (10 de 10 pruebas completadas exitosamente, 0 timeouts)
* Duración Promedio de Misión: 149.02 segundos   
  * *Mínima*: 145.73 s   
  * *Máxima*: 152.57 s
  * *Desviación estándar*: $\pm 2.61\text{ s}$   
* Pasos Físicos Totales por Misión: 60 pasos exactos (determinismo cinemático completo en las 10 pruebas)   
* Sobrecarga de Contexto (Tokens): 82.26 tokens promedio por paso   
* Latencia de Inferencia Promedio: 2.152 segundos por paso

### 6.2 Perfil de Latencia por Tipo de Operación Cognitiva

| Tipo de Decisión | Frecuencia por Misión | Tokens Típicos | Latencia Promedio (CPU) |
| :---: | :--- | :--- | :--- |
| **Disparo Directo en Mira** (Crosshair Bypass) | 4 interacciones | 0 tokens | **0.00 s** |
| **Paso Motor Ordinario** (Q3 - Movimiento/Giro) | ~50 decisiones | 59-72 tokens | **1.45 - 1.85 s** |
| **Percepción y Reorientación** (Q1 + Q3) | 3 – 4 eventos |	188 – 242 tokens | **5.50 – 7.20 s** |
| **Replanificación Estratégica Completa** (Q1 + Q-Precondición + Q2 + Q3) |	1 – 2 eventos |	313 – 383 tokens | **8.30 – 11.50 s** |