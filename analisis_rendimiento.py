import json
import os
import glob
import re
import statistics
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

CARPETA_VERSIONES = "./versiones"
ARCHIVO_SALIDA_GRAFICO = "comparativa_v3_en_adelante.png"

def clasificar_version(nombre_archivo, tag_modelo):
    """
    Identifica si un benchmark pertenece a la v3 en adelante.
    Descarta v1, v2, Ollama monolíticos o modelos previos.
    """
    texto = (nombre_archivo + " " + tag_modelo).lower()
    
    # Descartar versiones antiguas o LLMs generativos de las primeras pruebas
    if any(legacy in texto for legacy in ["_v1", "_v2", "qwen2.5:7b", "llama3", "phi3", "dual_qwen"]):
        return None
    
    if "v6" in texto or "fastreact" in texto or "agnostic" in texto:
        return "v6"
    elif "v5" in texto:
        return "v5"
    elif "v4" in texto:
        return "v4"
    elif "v3" in texto:
        return "v3"
        
    return None

def limpiar_etiqueta(tag_modelo, version):
    """Genera una etiqueta corta y legible para el eje X."""
    t = tag_modelo.replace("Pure_Decision_", "").replace("Egocentric_", "").replace("Agnostic_ReAct_", "")
    t = t.replace("FastReAct_", "").replace("_Mision_Estandar", "").replace("_Mision_Agnostica", "")
    t = re.sub(r"_TH[0-9.]+", "", t)
    return f"{version.upper()}: {t}"

def cargar_datos_v3_adelante():
    archivos = glob.glob(os.path.join(CARPETA_VERSIONES, "*.json"))
    if not archivos:
        print(f"No se encontraron archivos .json en {CARPETA_VERSIONES}")
        return pd.DataFrame()

    candidatos = []

    for ruta in sorted(archivos):
        nombre_archivo = os.path.basename(ruta)
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if not isinstance(data, list):
            data = [data]

        for entry in data:
            tag_modelo = entry.get("modelo") or entry.get("architecture") or nombre_archivo.replace(".json", "")
            pruebas = entry.get("pruebas", [])
            if not pruebas:
                continue

            version = clasificar_version(nombre_archivo, tag_modelo)
            if not version:
                continue  # Se ignora todo lo anterior a la v3

            total_pruebas = len(pruebas)
            exitosas = [p for p in pruebas if not p.get("supero_limite_tiempo", False)]
            tasa_exito = (len(exitosas) / total_pruebas) * 100.0

            duraciones = [p.get("duracion_total", 0.0) for p in exitosas]
            duracion_media = statistics.mean(duraciones) if duraciones else 999.0

            pasos_lista = [p.get("pasos_totales", len(p.get("decisiones", []))) for p in exitosas]
            pasos_medio = statistics.mean(pasos_lista) if pasos_lista else 0.0

            latencias = []
            tokens = []
            for p in pruebas:
                for d in p.get("decisiones", []):
                    lat = d.get("latencia", d.get("latency_seconds", 0.0))
                    tok = d.get("tokens_entrada", d.get("tokens", 0))
                    if lat > 0: latencias.append(lat)
                    if tok > 0: tokens.append(tok)

            lat_media = statistics.mean(latencias) if latencias else 0.0
            lat_mediana = statistics.median(latencias) if latencias else 0.0
            tok_medio = statistics.mean(tokens) if tokens else 0.0

            candidatos.append({
                "Version": version,
                "Etiqueta": limpiar_etiqueta(tag_modelo, version),
                "Tag": tag_modelo,
                "Pruebas": total_pruebas,
                "Exito": round(tasa_exito, 1),
                "Duracion": round(duracion_media, 2) if duraciones else 0.0,
                "Pasos": round(pasos_medio, 1),
                "Latencia_Media": round(lat_media, 3),
                "Latencia_Mediana": round(lat_mediana, 3),
                "Tokens": round(tok_medio, 1)
            })

    if not candidatos:
        return pd.DataFrame()

    df_todos = pd.DataFrame(candidatos)

    # Filtrar como máximo los 2 MEJORES modelos por cada versión
    seleccionados = []
    for ver in ["v3", "v4", "v5", "v6"]:
        df_ver = df_todos[df_todos["Version"] == ver]
        if df_ver.empty:
            continue
        # Criterio: Mayor éxito primero; en empate, menor duración de misión
        df_ver_sorted = df_ver.sort_values(by=["Exito", "Duracion"], ascending=[False, True])
        top_modelos = df_ver_sorted.head(2)
        seleccionados.append(top_modelos)

    if not seleccionados:
        return pd.DataFrame()

    return pd.concat(seleccionados, ignore_index=True)

def graficar_dashboard_limpio(df):
    if df.empty:
        print("No se encontraron registros de v3 en adelante.")
        return

    print("\n" + "="*85)
    print("        MODELOS SELECCIONADOS (TOP 2 POR VERSIÓN - DESDE V3 EN ADELANTE)")
    print("="*85)
    cols = ["Version", "Etiqueta", "Exito", "Duracion", "Pasos", "Latencia_Mediana", "Tokens"]
    print(df[cols].to_string(index=False))
    print("="*85 + "\n")

    n = len(df)
    indices = np.arange(n)
    etiquetas = df["Etiqueta"].tolist()

    fig, axs = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle("Evolución de Rendimiento: Versiones v3 a v6 (Voyager)", fontsize=15, fontweight="bold")

    # 1. Tasa de Éxito
    ax1 = axs[0, 0]
    colores_exito = ["#2ecc71" if e == 100 else "#e67e22" if e >= 50 else "#e74c3c" for e in df["Exito"]]
    bars1 = ax1.bar(indices, df["Exito"], color=colores_exito, edgecolor="black", width=0.5)
    ax1.set_title("Tasa de Éxito (% Misiones Completadas)", fontweight="bold")
    ax1.set_ylabel("Porcentaje (%)")
    ax1.set_ylim(0, 115)
    ax1.set_xticks(indices)
    ax1.set_xticklabels(etiquetas, rotation=20, ha="right", fontsize=9)
    ax1.grid(axis="y", linestyle="--", alpha=0.4)
    for b, dur in zip(bars1, df["Duracion"]):
        h = b.get_height()
        label = f"{h:.0f}%\n({dur}s)" if h > 0 else "0%"
        ax1.text(b.get_x() + b.get_width()/2, h + 2, label, ha="center", fontsize=8, fontweight="bold")

    # 2. Eficiencia Física (Pasos)
    ax2 = axs[0, 1]
    bars2 = ax2.bar(indices, df["Pasos"], color="#3498db", edgecolor="black", width=0.5)
    ax2.set_title("Eficiencia de Navegación (Pasos por Misión)", fontweight="bold")
    ax2.set_ylabel("Pasos Promedio")
    ax2.set_xticks(indices)
    ax2.set_xticklabels(etiquetas, rotation=20, ha="right", fontsize=9)
    ax2.grid(axis="y", linestyle="--", alpha=0.4)
    for b in bars2:
        h = b.get_height()
        if h > 0:
            ax2.text(b.get_x() + b.get_width()/2, h + 1, f"{h:.1f}", ha="center", fontsize=9, fontweight="bold")

    # 3. Latencia Cognitiva en CPU
    ax3 = axs[1, 0]
    ancho = 0.35
    ax3.bar(indices - ancho/2, df["Latencia_Media"], width=ancho, label="Media", color="#9b59b6", edgecolor="black")
    ax3.bar(indices + ancho/2, df["Latencia_Mediana"], width=ancho, label="Mediana", color="#f1c40f", edgecolor="black")
    ax3.set_title("Latencia de Inferencia por Paso (CPU)", fontweight="bold")
    ax3.set_ylabel("Segundos (s)")
    ax3.set_xticks(indices)
    ax3.set_xticklabels(etiquetas, rotation=20, ha="right", fontsize=9)
    ax3.legend()
    ax3.grid(axis="y", linestyle="--", alpha=0.4)

    # 4. Huella de Tokens
    ax4 = axs[1, 1]
    bars4 = ax4.bar(indices, df["Tokens"], color="#1abc9c", edgecolor="black", width=0.5)
    ax4.set_title("Sobrecarga de Contexto (Tokens/Paso)", fontweight="bold")
    ax4.set_ylabel("Tokens de Entrada")
    ax4.set_xticks(indices)
    ax4.set_xticklabels(etiquetas, rotation=20, ha="right", fontsize=9)
    ax4.grid(axis="y", linestyle="--", alpha=0.4)
    for b in bars4:
        h = b.get_height()
        if h > 0:
            ax4.text(b.get_x() + b.get_width()/2, h + 1, f"{h:.0f} t", ha="center", fontsize=9, fontweight="bold")

    plt.tight_layout()
    plt.savefig(ARCHIVO_SALIDA_GRAFICO, dpi=300)
    print(f"Gráfico limpio guardado en: '{ARCHIVO_SALIDA_GRAFICO}'")
    plt.show()

if __name__ == "__main__":
    df_filtrado = cargar_datos_v3_adelante()
    graficar_dashboard_limpio(df_filtrado)