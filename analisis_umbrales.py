import json
import os
import pandas as pd
import matplotlib.pyplot as plt
import re

JSON_PATH = "./versiones/Voyager_Architecture_v3.json"

def cargar_datos_umbrales():
    if not os.path.exists(JSON_PATH):
        print(f"Error: No se encontró el archivo {JSON_PATH}")
        return None
    
    with open(JSON_PATH, "r") as f:
        data = json.load(f)
        
    registros = []
    
    # Expresión regular para extraer el Motor y el Umbral del nombre
    # Ej: "Pure_Decision_LAYA_Sequential_TH0.45" -> Motor: LAYA, TH: 0.45
    patron = r'Pure_Decision_(LAYA|KEV)_.*?TH([0-9.]+)'
    
    for modelo in data:
        nombre = modelo.get("modelo", "")
        match = re.search(patron, nombre)
        
        if not match:
            continue
            
        motor = match.group(1)
        th = float(match.group(2))
        
        pruebas = modelo.get("pruebas", [])
        total_pruebas = len(pruebas)
        if total_pruebas == 0:
            continue
            
        timeouts = modelo.get("total_timeouts", 0)
        tasa_exito = ((total_pruebas - timeouts) / total_pruebas) * 100
        
        errores = modelo.get("total_errores_servidor_global", 0) + modelo.get("total_errores_chatarra_global", 0)
        overrides = modelo.get("total_overrides_confianza_global", 0)
        
        duracion_promedio = modelo.get("duracion_promedio", 0)
        
        registros.append({
            "Motor": motor,
            "Umbral (TH)": th,
            "Tasa de Exito (%)": tasa_exito,
            "Errores Lógicos": errores,
            "Anulaciones (Overrides)": overrides,
            "Duración Promedio (s)": duracion_promedio
        })
        
    df = pd.DataFrame(registros)
    # Ordenar por motor y luego por umbral para que las líneas se dibujen correctamente
    df = df.sort_values(by=["Motor", "Umbral (TH)"])
    return df

def generar_graficos_lineas(df):
    fig, axs = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle('Estudio Paramétrico: Impacto del Umbral de Confianza (Threshold)', fontsize=16, fontweight='bold')
    
    motores = df["Motor"].unique()
    colores = {"LAYA": "royalblue", "KEV": "darkorange"}
    marcadores = {"LAYA": "o", "KEV": "s"}

    # 1. Tasa de Éxito vs Umbral
    for motor in motores:
        df_motor = df[df["Motor"] == motor]
        axs[0, 0].plot(df_motor["Umbral (TH)"], df_motor["Tasa de Exito (%)"], 
                       marker=marcadores[motor], color=colores[motor], label=motor, linewidth=2)
    axs[0, 0].set_title('Tasa de Supervivencia (Sin Timeouts)')
    axs[0, 0].set_ylabel('Éxito (%)')
    axs[0, 0].set_xlabel('Umbral de Confianza (TH)')
    axs[0, 0].axhline(100, color='grey', linestyle='--', alpha=0.5)
    axs[0, 0].legend()
    axs[0, 0].grid(True, alpha=0.3)

    # 2. Errores Lógicos vs Umbral
    for motor in motores:
        df_motor = df[df["Motor"] == motor]
        axs[0, 1].plot(df_motor["Umbral (TH)"], df_motor["Errores Lógicos"], 
                       marker=marcadores[motor], color=colores[motor], label=motor, linewidth=2)
    axs[0, 1].set_title('Precisión Lógica: Reducción de Errores')
    axs[0, 1].set_ylabel('Cantidad de Errores Totales')
    axs[0, 1].set_xlabel('Umbral de Confianza (TH)')
    axs[0, 1].axhline(0, color='grey', linestyle='--', alpha=0.5)
    axs[0, 1].legend()
    axs[0, 1].grid(True, alpha=0.3)

    # 3. Intervenciones del Filtro (Overrides) vs Umbral
    for motor in motores:
        df_motor = df[df["Motor"] == motor]
        axs[1, 0].plot(df_motor["Umbral (TH)"], df_motor["Anulaciones (Overrides)"], 
                       marker=marcadores[motor], color=colores[motor], label=motor, linewidth=2)
    axs[1, 0].set_title('Actividad del Filtro (Anulaciones por Baja Certeza)')
    axs[1, 0].set_ylabel('Cantidad de Intervenciones')
    axs[1, 0].set_xlabel('Umbral de Confianza (TH)')
    axs[1, 0].legend()
    axs[1, 0].grid(True, alpha=0.3)

    # 4. Duración Promedio vs Umbral
    for motor in motores:
        df_motor = df[df["Motor"] == motor]
        # Ocultar los valores de 0 (cuando fallan todas las pruebas por timeout)
        df_valida = df_motor[df_motor["Duración Promedio (s)"] > 0]
        axs[1, 1].plot(df_valida["Umbral (TH)"], df_valida["Duración Promedio (s)"], 
                       marker=marcadores[motor], color=colores[motor], label=motor, linewidth=2)
    axs[1, 1].set_title('Eficiencia Física: Tiempo Promedio de Misión')
    axs[1, 1].set_ylabel('Segundos (s)')
    axs[1, 1].set_xlabel('Umbral de Confianza (TH)')
    axs[1, 1].legend()
    axs[1, 1].grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig("benchmark_umbrales.png", dpi=300)
    print("Gráfico guardado como 'benchmark_umbrales.png'")
    plt.show()

if __name__ == "__main__":
    df = cargar_datos_umbrales()
    if df is not None and not df.empty:
        print("\n=== Matriz Paramétrica de Rendimiento ===")
        print(df.to_string(index=False))
        generar_graficos_lineas(df)
    else:
        print("No hay datos de umbrales suficientes para graficar.")