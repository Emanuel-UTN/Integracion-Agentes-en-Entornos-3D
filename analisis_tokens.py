import json
import os
import pandas as pd
import matplotlib.pyplot as plt
import statistics

JSON_PATH = "./versiones/Voyager_Architecture_v4.json"

def cargar_datos_v4():
    if not os.path.exists(JSON_PATH):
        print(f"Error: No se encontró {JSON_PATH}")
        return None

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    filas = []
    for entry in data:
        modelo_tag = entry.get("modelo", "")
        pruebas = entry.get("pruebas", [])
        if not pruebas:
            continue

        total_pruebas = len(pruebas)
        timeouts = entry.get("total_timeouts", 0)
        exito_pct = ((total_pruebas - timeouts) / total_pruebas) * 100.0

        # Calcular pasos promedio y tokens acumulados por misión en pruebas exitosas
        pruebas_ok = [p for p in pruebas if not p.get("supero_limite_tiempo", False)]
        pasos_prom = statistics.mean([p["pasos_totales"] for p in pruebas_ok]) if pruebas_ok else 0
        
        tokens_mision_lista = [
            sum(d.get("tokens_entrada", 0) for d in p.get("decisiones", []))
            for p in pruebas_ok
        ]
        tokens_totales_prom = statistics.mean(tokens_mision_lista) if tokens_mision_lista else 0

        # Extraer certeza promedio en decisiones de movimiento/interacción (Q2)
        certezas_q2 = []
        for p in pruebas:
            for d in p.get("decisiones", []):
                pens = d.get("pensamiento", "")
                if "Conf:" in pens:
                    try:
                        c_val = float(pens.split("Conf:")[1].replace(")", "").strip())
                        certezas_q2.append(c_val)
                    except ValueError:
                        pass

        conf_prom = statistics.mean(certezas_q2) if certezas_q2 else 0.0

        filas.append({
            "Modelo": modelo_tag.replace("Egocentric_", ""),
            "Tokens/Paso": entry.get("tokens_promedio_global", 0),
            "Tokens Totales/Misión": round(tokens_totales_prom, 0),
            "Latencia Promedio (s)": entry.get("latencia_promedio", 0),
            "Duración Misión (s)": entry.get("duracion_promedio", 0),
            "Pasos Promedio": round(pasos_prom, 1),
            "Certeza Media Q2": round(conf_prom, 3),
            "Éxito (%)": round(exito_pct, 1)
        })

    df = pd.DataFrame(filas)
    return df.sort_values(by="Tokens/Paso")

def graficar_impacto_tokens(df):
    fig, axs = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle("Evaluación de Compresión de Tokens y Rendimiento Egocéntrico (Voyager v4)", fontsize=15, fontweight="bold")

    colores = ["#2ecc71" if ex == 100 else "#e74c3c" for ex in df["Éxito (%)"]]

    # 1. Tokens por Paso vs Latencia de Inferencia
    bars1 = axs[0, 0].bar(df["Modelo"], df["Tokens/Paso"], color="steelblue", edgecolor="black")
    axs[0, 0].set_title("Presupuesto de Tokens Promedio por Decisión")
    axs[0, 0].set_ylabel("Tokens de Entrada / Paso")
    axs[0, 0].grid(axis="y", alpha=0.3)
    for b in bars1:
        axs[0, 0].text(b.get_x() + b.get_width()/2, b.get_height() + 2, f"{b.get_height():.1f} tok", ha="center", fontweight="bold")

    # 2. Latencia Promedio por Paso vs Nivel de Compresión
    bars2 = axs[0, 1].bar(df["Modelo"], df["Latencia Promedio (s)"], color="darkorange", edgecolor="black")
    axs[0, 1].set_title("Latencia Cognitiva Promedio por Paso (CPU)")
    axs[0, 1].set_ylabel("Segundos (s)")
    axs[0, 1].grid(axis="y", alpha=0.3)
    for b in bars2:
        axs[0, 1].text(b.get_x() + b.get_width()/2, b.get_height() + 0.05, f"{b.get_height():.2f}s", ha="center", fontweight="bold")

    # 3. Duración Total de la Misión (Solo modelos con éxito > 0)
    bars3 = axs[1, 0].bar(df["Modelo"], df["Duración Misión (s)"], color=colores, edgecolor="black")
    axs[1, 0].set_title("Tiempo Total de Misión (Verde = 100% Éxito | Rojo = Timeout)")
    axs[1, 0].set_ylabel("Duración Promedio (s)")
    axs[1, 0].grid(axis="y", alpha=0.3)
    for idx, b in enumerate(bars3):
        val = b.get_height()
        label = f"{val:.1f}s" if val > 0 else "TIMEOUT"
        axs[1, 0].text(b.get_x() + b.get_width()/2, val + 3, label, ha="center", fontweight="bold")

    # 4. Certeza Media del Modelo (Confianza en Q2) vs Tokens
    bars4 = axs[1, 1].bar(df["Modelo"], df["Certeza Media Q2"], color="mediumpurple", edgecolor="black")
    axs[1, 1].set_title("Certeza Matemática Media del Modelo en Navegación (Q2)")
    axs[1, 1].set_ylabel("Confidence (0.0 a 1.0)")
    axs[1, 1].grid(axis="y", alpha=0.3)
    for b in bars4:
        axs[1, 1].text(b.get_x() + b.get_width()/2, b.get_height() + 0.01, f"{b.get_height():.3f}", ha="center", fontweight="bold")

    for ax in axs.flat:
        ax.tick_params(axis='x', rotation=15)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig("benchmark_tokens_v4.png", dpi=300)
    print("\nGráfico guardado como 'benchmark_tokens_v4.png'")
    plt.show()

if __name__ == "__main__":
    df = cargar_datos_v4()
    if df is not None and not df.empty:
        print("\n=== TABLA COMPARATIVA DE COMPRESIÓN DE TOKENS (V4) ===")
        print(df.to_string(index=False))
        graficar_impacto_tokens(df)