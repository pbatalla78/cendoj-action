# main.py v1.7 — Acción CENDOJ
# Cambios clave:
# - Ranking híbrido como fallback (relevancia + actualidad)
# - Notas explicativas estructuradas (Motivo/Acción/Sugerencias/Info)
# - Sugerencia de sinónimos específicas si 0 resultados
# - Enlace estable prioriza CENDOJ indexAN.jsp + secundaria por ECLI/ROJ
# - Validación HEAD con stdlib (urllib) -> sin httpx/requests

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import ssl
import math

app = FastAPI(title="CENDOJ Action", version="1.7")

# ---------- Utilidades ----------

def normalize_text(s: str) -> str:
    return " ".join(s.lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").split())

SINONIMOS = {
    "fuera de ordenacion": ["situacion de fuera de ordenacion", "no ajustado a ordenacion", "edificacion disconforme"],
    "volumen disconforme": ["edificacion disconforme", "exceso de volumen"],
    "suelo no urbanizable": ["suelo rustico", "suelos protegidos", "suelo no apto para urbanizar"],
    "garaje ilegal": ["aparcamiento ilegal", "cochera sin licencia"],
    "ordenacion": ["planeamiento", "planeacion", "ordenacion urbanistica"]
}

def sugerencias_para_query(q: str) -> List[str]:
    nq = normalize_text(q)
    res = []
    for clave, sins in SINONIMOS.items():
        if clave in nq:
            res.extend(sins[:3])
    # si no hubo match directo, proponemos 2 sinónimos frecuentes de urbanístico
    if not res:
        res = ["ajusta fechas/órgano", "prueba con términos más generales"]
    return res[:3]

def parse_date(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None

def validar_rango(desde: Optional[str], hasta: Optional[str]) -> (Optional[date], Optional[date], Optional[str]):
    d1 = parse_date(desde)
    d2 = parse_date(hasta)
    nota = None
    if d1 and d2 and d1 > d2:
        # corregimos de forma segura
        d1, d2 = d2, d1
        nota = "Se corrigió el rango de fechas (invertido)."
    return d1, d2, nota

def head_ok(url: str, timeout: float = 5.0) -> bool:
    # Validación HEAD sin dependencias
    try:
        ctx = ssl.create_default_context()
        req = Request(url, method="HEAD", headers={"User-Agent": "CENDOJ-Action/1.7"})
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            # 2xx/3xx lo consideramos OK
            return 200 <= resp.status < 400
    except (HTTPError, URLError, ssl.SSLError):
        return False
    except Exception:
        return False

def score_hibrido(relevancia_0_1: float, fecha_iso: str) -> float:
    """
    Ranking híbrido simple: 70% relevancia + 30% actualidad
    Actualidad = exp(-años_transcurridos) -> 1 muy reciente, decae con el tiempo
    """
    try:
        f = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
        years = max(0.0, (date.today() - f).days / 365.25)
        actualidad = math.exp(-years)  # 1 si hoy; ~0.37 si 1 año; ~0.14 si 2 años
    except Exception:
        actualidad = 0.5
    return 0.7 * max(0.0, min(1.0, relevancia_0_1)) + 0.3 * actualidad

def enlace_estable_cendoj() -> str:
    # Página oficial del buscador CENDOJ (sin parámetros, el usuario introduce ECLI/ROJ/Id)
    return "https://www.poderjudicial.es/search/indexAN.jsp"

def enlace_estable_secundario_por_ecli(ecli: str, roj: str) -> str:
    # Búsqueda estable pública (secundaria) por ECLI/ROJ
    from urllib.parse import quote_plus
    q = f'site:poderjudicial.es {ecli if ecli else ""} {roj if roj else ""}'.strip()
    return f"https://www.google.com/search?q={quote_plus(q)}"

def construir_nota(*, motivo: Optional[str]=None, accion: Optional[str]=None,
                   sugerencias: Optional[List[str]]=None, info: Optional[str]=None) -> str:
    partes = []
    if motivo:
        partes.append(f"Motivo: {motivo}")
    if accion:
        partes.append(f"Acción: {accion}")
    if sugerencias:
        partes.append("Sugerencias: " + "; ".join(sugerencias))
    if info:
        partes.append(f"Info: {info}")
    return " ".join(partes) if partes else None

# ---------- Datos simulados (para las URLs del smoke test) ----------
# Nota: Se mantienen los mismos IDs/ECLI/ROJ usados en pruebas previas
BASE_CASOS = [
    {
        "match": "urbanizable",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "resumen": "Licencia urbanística en suelo no urbanizable: criterios recientes (Tribunal Superior de Justicia de Cataluña (TSJC) - Sala de lo Contencioso-Administrativo) Fecha: 2024-02-12.",
        "id_cendoj": "0801932001202400077",
        "roj": "STS 1234/2024",
        "ecli": "ECLI:ES:TS:2024:1234"
    },
    {
        "match": "ordenacion",  # para prueba 5 y 8
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "relevancia": 0.82,
        "resumen": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano (Tribunal Supremo (TS) - Sala Tercera (Cont.-Adm.)) Fecha: 2022-11-03.",
        "id_cendoj": "28079130012022000456",
        "roj": "STS 456/2022",
        "ecli": "ECLI:ES:TS:2022:456"
    }
]

# Búsqueda reescrita con sinónimos si procede
def expandir_query_con_sinonimos(q: str) -> List[str]:
    nq = normalize_text(q)
    terms = [q]
    for clave, sins in SINONIMOS.items():
        if clave in nq:
            terms.extend(sins)
    return list(dict.fromkeys(terms))  # únicos y en orden

# Filtrado simulado por fecha
def filtra_por_fecha(casos: List[Dict[str,Any]], d1: Optional[date], d2: Optional[date]) -> List[Dict[str,Any]]:
    res = []
    for c in casos:
        f = datetime.strptime(c["fecha"], "%Y-%m-%d").date()
        if d1 and f < d1:
            continue
        if d2 and f > d2:
            continue
        res.append(c)
    return res

# ---------- Endpoint principal ----------

@app.get("/buscar-cendoj")
def buscar_cendoj(
    query: str = Query(..., description="Términos de búsqueda"),
    organo: Optional[str] = Query(None),
    desde: Optional[str] = Query(None),
    hasta: Optional[str] = Query(None),
    orden: str = Query("relevancia_desc", regex="^(relevancia_desc|fecha_desc|fecha_asc)$"),
    limite: int = Query(10, ge=1, le=50),
    validar_enlaces: bool = Query(False)
):
    original_query = query
    nota_partes = []

    # 1) Normalización + corrección de fechas
    d1, d2, nota_fechas = validar_rango(desde, hasta)
    if nota_fechas:
        nota_partes.append(nota_fechas)

    # 2) Expansión por sinónimos (solo para matching simulado)
    variantes = expandir_query_con_sinonimos(query)

    # 3) "Consulta" a nuestro set simulado
    candidatos: List[Dict[str,Any]] = []
    nq = normalize_text(query)
    for caso in BASE_CASOS:
        if caso["match"] in nq:
            candidatos.append(caso.copy())
        else:
            # coincide con alguna variante?
            if any(normalize_text(v) in caso["match"] or caso["match"] in normalize_text(v) for v in variantes):
                candidatos.append(caso.copy())

    # Filtro por órgano (si lo piden, match simple)
    if organo:
        cand_filtrados = []
        no = normalize_text(organo)
        for c in candidatos:
            if no in normalize_text(c["organo"]):
                cand_filtrados.append(c)
        candidatos = cand_filtrados

    # Filtro de fechas
    candidatos = filtra_por_fecha(candidatos, d1, d2)

    # 4) Ordenación
    def sort_key(c):
        if orden == "fecha_desc":
            return (c["fecha"], c["relevancia"])
        elif orden == "fecha_asc":
            return (c["fecha"], c["relevancia"])
        else:
            # relevancia_desc (primario)
            return (c["relevancia"], c["fecha"])

    reverse = True if orden in ("relevancia_desc", "fecha_desc") else False
    candidatos.sort(key=sort_key, reverse=reverse)

    # 5) Fallback a ranking híbrido si tras filtros/orden queda vacío o hay empate pobre
    uso_hibrido = False
    if not candidatos:
        # Intento híbrido con todo el corpus que coincida grosso modo con el texto
        pool = []
        for c in BASE_CASOS:
            if any(normalize_text(c["match"]) in normalize_text(v) or normalize_text(v) in normalize_text(c["match"]) for v in variantes):
                pool.append(c.copy())
        if pool:
            for c in pool:
                c["_score_hibrido"] = score_hibrido(c["relevancia"], c["fecha"])
            pool.sort(key=lambda x: x["_score_hibrido"], reverse=True)
            candidatos = pool
            uso_hibrido = True
    else:
        # Si hay resultados pero orden pobre (p.ej. todos muy antiguos), aplicamos reordenación híbrida suave
        # (solo si el usuario pidió relevancia_desc)
        if orden == "relevancia_desc":
            for c in candidatos:
                c["_score_hibrido"] = score_hibrido(c["relevancia"], c["fecha"])
            candidatos.sort(key=lambda x: (x["_score_hibrido"]), reverse=True)
            uso_hibrido = True

    # 6) Construcción de resultados con enlaces y validación
    resultados = []
    for c in candidatos[:limite]:
        id_cendoj = c["id_cendoj"]
        ecli = c["ecli"]
        roj = c["roj"]

        url_directo = f"https://www.poderjudicial.es/search/cedula.jsp?id={id_cendoj}"
        url_estable = enlace_estable_cendoj()
        url_estable_sec = enlace_estable_secundario_por_ecli(ecli, roj)

        enlace_directo_ok = None
        estrategia_enlace = "directo"
        enlace_preferido = url_directo

        nota_enlace = None
        if validar_enlaces:
            ok = head_ok(url_directo)
            enlace_directo_ok = bool(ok)
            if not ok:
                estrategia_enlace = "estable"
                enlace_preferido = url_estable
                nota_enlace = f"El enlace directo de {id_cendoj} no respondió como esperado; se usa enlace estable (ECLI/ROJ)."

        # Resultado
        resultados.append({
            "titulo": c["titulo"],
            "organo": c["organo"],
            "sala": c["sala"],
            "ponente": c["ponente"],
            "fecha": c["fecha"],
            "relevancia": round(c["relevancia"], 2),
            "resumen": c["resumen"],
            "id_cendoj": id_cendoj,
            "roj": roj,
            "ecli": ecli,
            "url_directo": url_directo,
            "url_estable": url_estable,
            "url_estable_secundaria": url_estable_sec,
            "enlace_preferido": enlace_preferido,
            "enlace_directo_ok": enlace_directo_ok,
            "estrategia_enlace": estrategia_enlace
        })

        if nota_enlace:
            nota_partes.append(nota_enlace)

    # 7) 0 resultados tras todo -> nota con sinónimos concretos
    nota_final = None
    if not resultados:
        sins = sugerencias_para_query(original_query)
        nota_final = construir_nota(
            motivo="No se han encontrado resultados exactos.",
            accion="Ajusta términos o el rango temporal.",
            sugerencias=sins,
            info="Se intentó ranking híbrido (relevancia + actualidad)."
        )
    else:
        info = f"orden: {'ranking híbrido (relevancia + actualidad)' if uso_hibrido else orden}"
        # integrar notas acumuladas + info
        nota_final = construir_nota(
            motivo=None,
            accion=None,
            sugerencias=None,
            info=info
        )
        if nota_partes:
            nota_final = (nota_final + " ").strip() + " " + " ".join(nota_partes)

    payload = {
        "query": original_query,
        "total": len(resultados),
        "resultados": resultados,
        "nota": nota_final if nota_final else None
    }
    return JSONResponse(payload)