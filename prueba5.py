import tableauserverclient as TSC
import polars as pl
import io
import os
import re

# --- 1. DATOS DE CONEXIÓN GLOBAL (100% SEGUROS DESDE VARIABLES DE ENTORNO) ---
TOKEN_NAME = os.environ.get('TABLEAU_TOKEN_NAME') 
TOKEN_VALUE = os.environ.get('TABLEAU_TOKEN_VALUE') 
SERVER_URL = os.environ.get('TABLEAU_SERVER_URL') 
SITE_ID = os.environ.get('TABLEAU_SITE_ID') 
TARGET_WORKBOOK_NAME = os.environ.get('TABLEAU_WORKBOOK_NAME') 

# Carpeta local o del volumen de Easypanel donde caerá todo
OUTPUT_DIR = os.environ.get('DATA_DIR', 'data_sistema')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Validación de seguridad: detener el script en seco si falta alguna credencial clave
credenciales_faltantes = [
    k for k, v in {
        'TABLEAU_TOKEN_NAME': TOKEN_NAME,
        'TABLEAU_TOKEN_VALUE': TOKEN_VALUE,
        'TABLEAU_SERVER_URL': SERVER_URL,
        'TABLEAU_SITE_ID': SITE_ID,
        'TABLEAU_WORKBOOK_NAME': TARGET_WORKBOOK_NAME
    }.items() if not v
]

if credenciales_faltantes:
    raise ValueError(f"❌ ERROR DE SEGURIDAD: Faltan las siguientes Variables de Entorno obligatorias: {credenciales_faltantes}")


# --- 2. FUNCIÓN MAESTRA DE DESCARGA Y REESTRUCTURACIÓN DE TIPOS ---
def extraer_pestaña_a_parquet(server, wb, view_name, nombre_archivo_parquet, filtros=None):
    """
    Busca una pestaña específica dentro del workbook, le aplica filtros opcionales,
    asegura las conversiones numéricas exactas solicitadas, estandariza todas las columnas
    a minúsculas y snake_case (reglas de base de datos), y guarda un Parquet limpio.
    """
    print(f"\n🔍 Buscando la pestaña: '{view_name}'...")
    view = next((v for v in wb.views if v.name == view_name), None)

    if not view:
        print(f"❌ No encontré la pestaña '{view_name}'.")
        print("Pestañas disponibles en este dashboard:", [v.name for v in wb.views])
        return False

    try:
        # Configurar filtros si se enviaron
        opciones_filtro = TSC.CSVRequestOptions()
        if filtros:
            print(f"⚙️ Aplicando filtros para {view_name}...")
            for campo, valor in filtros.items():
                opciones_filtro.vf(campo, valor)

        # Extraer el CSV de Tableau a memoria
        print(f"🚀 Descargando datos desde Tableau...")
        server.views.populate_csv(view, opciones_filtro)
        contenido_bytes = b"".join(view.csv)

        if len(contenido_bytes) == 0:
            print(f"⚠️ La descarga devolvió 0 bytes. Verifica si los filtros son muy restrictivos.")
            return False

        # 1. Leer el CSV inicialmente como texto (String) para evitar truncados accidentales
        print(f"⚡ Procesando y aplicando ingeniería de tipos...")
        df = pl.read_csv(io.BytesIO(contenido_bytes), infer_schema_length=0)

        # --- REGLA MAESTRA INYECTADA: NORMALIZACIÓN DE COLUMNAS ---
        # Convierte a minúsculas, remueve caracteres especiales y cambia espacios por '_'
        columnas_limpias = []
        for col in df.columns:
            col_limpia = col.lower()
            col_limpia = re.sub(r'[\(\),.]', '', col_limpia)  # Remueve (, ), comas y puntos
            col_limpia = re.sub(r'\s+', '_', col_limpia)      # Cambia espacios o tabs por un solo guion bajo
            col_limpia = col_limpia.strip('_')                # Quita guiones bajos huérfanos en los extremos
            columnas_limpias.append(col_limpia)
        
        df.columns = columnas_limpias

        # CASO A: Reporte Dinámico de Clientes (Contiene Measure Names y Measure Values normalizados)
        if "measure_names" in df.columns and "measure_values" in df.columns:
            print("🔄 Estructura Multi-Medida detectada. Limpiando y convirtiendo 'measure_values' a Float64...")
            
            # Limpiamos caracteres de formato (comas) y casteamos a Float antes del Pivot
            df = df.with_columns(
                pl.col("measure_values")
                .str.replace_all(",", "")
                .str.strip_chars()
                .cast(pl.Float64, strict=False)
            )
            
            # Identificar las dimensiones que actuarán como llaves (las que se quedarán como String)
            columnas_llave = [c for c in df.columns if c not in ["measure_names", "measure_values"]]
            
            print("🔀 Pivoteando la tabla horizontalmente...")
            df = df.pivot(
                on="measure_names",
                values="measure_values",
                index=columnas_llave,
                aggregate_function="first"
            )

            # Volvemos a normalizar los nombres de columnas porque las nuevas columnas que nacieron
            # del Pivot adoptan los nombres que traían los datos en crudo (Ej: "VENTA CF" -> "venta_cf")
            columnas_pivot_limpias = []
            for col in df.columns:
                col_limpia = col.lower()
                col_limpia = re.sub(r'[\(\),.]', '', col_limpia)
                col_limpia = re.sub(r'\s+', '_', col_limpia)
                col_limpia = col_limpia.strip('_')
                columnas_pivot_limpias.append(col_limpia)
            df.columns = columnas_pivot_limpias

        # CASO B: Reporte de Estructura Plana (Como Coberturas)
        else:
            print("📋 Estructura Plana detectada. Buscando y transformando columnas de conteos numéricos...")
            
            # Identificamos dinámicamente si existe la columna "clientes" o totales basándonos en minúsculas
            for col in df.columns:
                if "clientes" in col or "total" in col or col == "index":
                    df = df.with_columns(
                        pl.col(col)
                        .str.replace_all(",", "")
                        .str.strip_chars()
                        .cast(pl.Float64, strict=False)
                    )

        # 2. Ruta final y persistencia optimizada
        ruta_final = os.path.join(OUTPUT_DIR, nombre_archivo_parquet)
        df.write_parquet(ruta_final)
        
        print(f"💾 ✅ ¡LOGRADO! Se guardó '{ruta_final}' con formato limpio y tipos listos ({df.shape[0]} filas).")
        return True

    except Exception as e:
        print(f"❌ Error procesando la pestaña '{view_name}': {str(e)}")
        return False


# --- 3. PROCESO PRINCIPAL ---
def ejecutar_ingesta_completa():
    tableau_auth = TSC.PersonalAccessTokenAuth(TOKEN_NAME, TOKEN_VALUE, SITE_ID)
    server = TSC.Server(SERVER_URL, use_server_version=True)

    with server.auth.sign_in(tableau_auth):
        print(f"✅ Conectado al sitio de Tableau: {SITE_ID}")

        # Cargar el Workbook una sola vez para todas las descargas
        all_workbooks, _ = server.workbooks.get()
        wb = next((w for w in all_workbooks if w.name == TARGET_WORKBOOK_NAME), None)
        
        if not wb:
            print(f"❌ No encontré el Workbook '{TARGET_WORKBOOK_NAME}'.")
            return

        # Popular todas las vistas del libro de una sola vez
        server.workbooks.populate_views(wb)

        # -----------------------------------------------------------------
        # CONFIGURACIÓN DE TUS DESCARGAS
        # -----------------------------------------------------------------
        
        # TABLA 1: Clientes Georreferenciados
        filtros_clientes = {'Mes, Año de fecha_liquidacion': 'mayo de 2026'}
        extraer_pestaña_a_parquet(
            server, 
            wb, 
            view_name='Clientes Georeferenciados', 
            nombre_archivo_parquet='clientes.parquet', 
            filtros=filtros_clientes
        )

        # TABLA 2: Análisis de Rutas
        filtros_rutas = {'Mes, Año de fecha_liquidacion': 'mayo de 2026'}
        extraer_pestaña_a_parquet(
            server, 
            wb, 
            view_name='DATA COBERTURAS GT', 
            nombre_archivo_parquet='coberturas_rutas.parquet', 
            filtros=filtros_rutas
        )

        print("\n🏁 Proceso de ingesta finalizado.")


if __name__ == "__main__":
    ejecutar_ingesta_completa()
