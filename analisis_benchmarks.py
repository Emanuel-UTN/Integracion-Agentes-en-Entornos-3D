import json
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Ruta al archivo JSON generado por Unity/FastAPI
JSON_PATH = "./versiones/Voyager_Architecture_v1.json"

def cargar_datos():
    if not os.path.exists(JSON_PATH):
        print(f"Error: No se encontró el archivo {JSON_PATH}")
        return None
    
    with open(JSON_PATH, "r") as f:
        data = json.load(f)
        
    registros = []
    for modelo in data:
        nombre = modelo.get("modelo", "Desconocido")
        if not nombre: 
            continue
            
        pruebas = modelo.get("pruebas", [])
        total_pruebas = len(pruebas)
        if total_pruebas == 0:
            continue
            
        timeouts = sum(1 for p in pruebas if p.get("supero_limite_tiempo", False))
        tasa_exito = ((total_pruebas - timeouts) / total_pruebas) * 100
        
        errores_chatarra = sum(p.get("errores_interaccion_chatarra", 0) for p in pruebas)
        errores_servidor = sum(p.get("errores_servidor_prematuro", 0) for p in pruebas)
        total_errores = errores_chatarra + errores_servidor
        
        latencia_mediana = modelo.get("latencia_mediana", 0)
        
        # Solo promediamos la duración de las pruebas que NO fallaron por timeout
        duraciones_exitosas = [p.get("duracion_total", 0) for p in pruebas if not p.get("supero_limite_tiempo", False)]
        duracion_promedio = sum(duraciones_exitosas) / len(duraciones_exitosas) if duraciones_exitosas else 0
        
        registros.append({
            "Modelo": nombre,
            "Tasa de Exito (%)": tasa_exito,
            "Total Errores Lógicos": total_errores,
            "Latencia Mediana (s)": latencia_mediana,
            "Duración Promedio (s)": duracion_promedio
        })
        
    return pd.DataFrame(registros)

def generar_graficos(df):
    # Configuramos el lienzo con 4 subgráficos (2x2)
    fig, axs = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Análisis de Rendimiento Cognitivo y Físico (Voyager)', fontsize=16, fontweight='bold')
    
    colores = plt.cm.get_cmap('viridis', len(df))

    # 1. Tasa de Éxito (Resolución sin Timeout)
    ax = axs[0, 0]
    bars = ax.bar(df["Modelo"], df["Tasa de Exito (%)"], color=colores(np.linspace(0, 1, len(df))))
    ax.set_title('Tasa de Éxito (Completado < 500s)', fontweight='bold')
    ax.set_ylabel('Porcentaje (%)')
    ax.set_ylim(0, 110)
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2, f'{bar.get_height():.1f}%', ha='center')

    # 2. Errores Lógicos (Chatarra + Servidor prematuro)
    ax = axs[0, 1]
    bars = ax.bar(df["Modelo"], df["Total Errores Lógicos"], color='tomato')
    ax.set_title('Robustez: Total de Errores Lógicos', fontweight='bold')
    ax.set_ylabel('Cantidad de Errores')
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, f'{int(bar.get_height())}', ha='center')

    # 3. Latencia Mediana por Decisión
    ax = axs[1, 0]
    bars = ax.bar(df["Modelo"], df["Latencia Mediana (s)"], color='skyblue')
    ax.set_title('Velocidad Cognitiva: Latencia Mediana', fontweight='bold')
    ax.set_ylabel('Segundos (s)')
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, f'{bar.get_height():.1f}s', ha='center')

    # 4. Duración Promedio de la Misión (Solo éxitos)
    ax = axs[1, 1]
    bars = ax.bar(df["Modelo"], df["Duración Promedio (s)"], color='lightgreen')
    ax.set_title('Eficiencia Física: Duración Promedio de Misión', fontweight='bold')
    ax.set_ylabel('Segundos (s)')
    for bar in bars:
        if bar.get_height() > 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2, f'{bar.get_height():.1f}s', ha='center')
        else:
            ax.text(bar.get_x() + bar.get_width()/2, 5, 'N/A', ha='center')

    plt.setp(axs[0, 0].xaxis.get_majorticklabels(), rotation=15, ha="right")
    plt.setp(axs[0, 1].xaxis.get_majorticklabels(), rotation=15, ha="right")
    plt.setp(axs[1, 0].xaxis.get_majorticklabels(), rotation=15, ha="right")
    plt.setp(axs[1, 1].xaxis.get_majorticklabels(), rotation=15, ha="right")

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    # Guardar gráfico y mostrar
    plt.savefig("benchmark_resultados.png", dpi=300)
    plt.show()

if __name__ == "__main__":
    df = cargar_datos()
    if df is not None and not df.empty:
        print("=== Tabla de Resultados ===")
        print(df.to_string(index=False))
        generar_graficos(df)
    else:
        print("No hay datos suficientes para graficar.")