from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import duckdb
import os

app = FastAPI(
    title="Motor Analítico Comercial (DuckDB + Parquet)",
    description="Microservicio de alta velocidad para Agentes de IA y Aplicaciones",
    version="1.0.0"
)

# Carpeta de producción donde Easypanel guardará los Parquet (vía volumen montado)
DATA_DIR = os.getenv("DATA_DIR", "/app/data_sistema")

# Estructura esperada para la petición HTTP POST
class QueryModel(BaseModel):
    sql: str

@app.get("/")
def read_root():
    return {"status": "online", "motor": "DuckDB + Parquet ready"}

@app.post("/query")
def ejecutar_consulta_analitica(payload: QueryModel):
    """
    Recibe una consulta SQL en minúsculas, la ejecuta sobre los Parquet en el VPS
    y retorna un JSON perfectamente estructurado.
    """
    try:
        # Conexión instantánea en memoria
        con = duckdb.connect(database=':memory:')
        
        # Mapeo lógico de rutas a nombres de tablas limpios
        ruta_clientes = os.path.join(DATA_DIR, "clientes.parquet")
        ruta_coberturas = os.path.join(DATA_DIR, "coberturas_rutas.parquet")
        
        vistas_creadas = 0
        
        if os.path.exists(ruta_clientes):
            con.execute(f"CREATE VIEW clientes AS SELECT * FROM '{ruta_clientes}'")
            vistas_creadas += 1
            
        if os.path.exists(ruta_coberturas):
            con.execute(f"CREATE VIEW coberturas AS SELECT * FROM '{ruta_coberturas}'")
            vistas_creadas += 1
            
        if vistas_creadas == 0:
            return {
                "status": "warning",
                "message": f"Aún no hay archivos Parquet en '{DATA_DIR}'. Ejecuta el script de ingesta primero.",
                "data": []
            }
            
        # Ejecutar la consulta del usuario o agente de IA
        df_resultado = con.execute(payload.sql).df()
        
        # Transformar a formato JSON estándar (Lista de diccionarios)
        datos_json = df_resultado.to_dict(orient="records")
        
        return {
            "status": "success",
            "rows": len(datos_json),
            "data": datos_json
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error en SQL: {str(e)}")
