# main.py — CENDOJ Action (v1.16)
# - Sin dependencias externas: usa urllib para validar enlaces (evita ModuleNotFoundError: httpx)
# - Mejores: normalización + sinónimos, ranking híbrido (relevancia + actualidad),
#   resúmenes consistentes, notas explicativas, filtros temporales explícitos,
#   enlaces híbridos (directo + estable) con validación opcional.

import re
import math
import urllib.request
from datetime import datetime, date
from typing import List, Optional, Literal, Dict, Any

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="CENDOJ Action",
    version="1.16",
    description="Buscador auxiliar con normalización, sinónimos, ranking híbrido, resúmenes y validación de enlaces (sin dependencias externas)."
)

DATE_FMT = "%Y-%m-%d"

# ---------------------------
# Utilidades
# ---------------------------

def to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s, DATE_FMT).date()

def today() -> date:
    return date.today()

def pct_int(v: float) -> int:
    v = max(0.0, min(1.0, float(v)))
    return int(round(v * 100))

def normaliza_organo(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    k = re.sub(r"\s+", " ", s.strip().lower())
    mapping = {
        "tribunal supremo": "Tribunal Supremo (TS)",
        "ts": "Tribunal Supremo (TS)",
        "audiencia nacional": "Audiencia Nacional (AN)",
        "an": "Audiencia Nacional (AN)",
        "tsjc": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "tribunal superior de justicia de cataluña": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    }
    return mapping.get(k, s)

# Sinónimos (expansión simple)
SYNONYMS: Dict[str, List[str]] = {
    "fuera de ordenación": ["situación de fuera de ordenación", "edificación disconforme", "ordenación urbanística"],
    "volumen disconforme": ["alteración de volumen", "aumento de edificabilidad", "disconformidad con planeamiento"],
    "suelo no urbanizable": ["suelo rústico", "suelos protegidos", "autorización excepcional"],
}

def expand_query(q: str) -> (str, List[str]):
    usados: List[str] = []
    ql = q.lower()
    for base, exps in SYNONYMS.items():
        if base in ql:
            usados.extend(exps)
    return q, usados

# ---------------------------
# Dataset DEMO (sustituir por fuente real si procede)
# ---------------------------

DEMO_DOCS = [
    {
        "id_cendoj": "0801932001202400077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "roj": "STS 1234/2024",
        "ecli": "ECLI:ES:TS:2024:1234",
    },
    {
        "id_cendoj": "08019320012023000123",
        "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente A",
        "fecha": "2023-05-10",
        "relevancia": 0.18,
        "roj": "STS 111/2023",
        "ecli": "ECLI:ES:TS:2023:111",
    },
    {
        "id_cendoj": "28079130012022000456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "relevancia": 0.82,
        "roj": "STS 456/2022",
        "ecli": "ECLI:ES:TS:2022:456",
    },
]

# ---------------------------
# Modelos de respuesta
# ---------------------------

class Resultado(BaseModel):
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: str
    relevancia: int  # %
    resumen: Optional[str] = None
    id_cendoj: Optional[str] = None
    roj: Optional[str] = None
    ecli: Optional[str] = None
    url_directo: Optional[str] = None
    url_estable: Optional[str] = None
    enlace_preferido: Optional[str] = None
    enlace_directo_ok: Optional[bool] = None
    estrategia_enlace: Literal["directo", "estable"] = "directo"

class Respuesta(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None

# ---------------------------
# Resúmenes consistentes
# ---------------------------

def make_resumen(titulo: str, organo: str, sala: Optional[str], fecha: str) -> str:
    base = re.sub(r"\s+", " ", (titulo or "")).strip().rstrip(".")
    partes = [base]
    if organo:
        partes.append(f"({organo}{' - ' + sala if sala else ''})")
    if fecha:
        partes.append(f"Fecha: {fecha}.")
    return " ".join(partes)

# ---------------------------
# Enlaces
# ---------------------------

def enlace_directo(id_cendoj: str) -> str:
    return f"https://www.poderjudicial.es/search/cedula.jsp?id={id_cendoj}"

def enlace_estable(roj: Optional[str], ecli: Optional[str]) -> str:
    # Preferimos búsqueda por ECLI; si no hay, por ROJ; si no, genérica
    if ecli:
        q = f"site%3Apoderjudicial.es+%22{ecli}%22"
    elif roj:
        q = f"site%3Apoderjudicial.es+%22{roj}%22"
    else:
        q = "site%3Apoderjudicial.es+CENDOJ"
    return f"https://www.google.com/search?q={q}"

def valida_directo_stdlib(url: str, timeout_s: float = 2.5) -> Optional[bool]:
    """
    Validación best-effort sin dependencias:
    - Hace GET con urllib (User-Agent propio).
    - Devuelve True si 200 y no contiene '404 Page Error'; False si >= 400 o excepción.
    - Devuelve None si no es concluyente (dejar al llamador decidir).
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CendojAction/1.16)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.getcode()
            if status == 200:
                # Leemos un pequeño fragmento para detectar la página de error
                snippet = resp.read(2048).decode("utf-8", errors="ignore")
                if "404 Page Error" in snippet:
                    return False
                return True
            if status >= 400:
                return False
            return None
    except Exception:
        return False

# ---------------------------
# Matching y ranking
# ---------------------------

def matches(q: str, doc: Dict[str, Any]) -> bool:
    hay = (doc["titulo"] + " " + doc["organo"] + " " + (doc.get("sala") or "")).lower()
    tokens = re.findall(r"\w+", q.lower())
    return all(t in hay for t in tokens) if tokens else True

def buscar_base(q: str, sinonimos: List[str]) -> (List[Dict[str, Any]], List[str]):
    notas: List[str] = []
    candidatos: List[Dict[str, Any]] = []
    for d in DEMO_DOCS:
        if matches(q, d):
            candidatos.append(d)
    if not candidatos and sinonimos:
        notas.append("Se usaron sinónimos comunes para ampliar la coincidencia.")
        for d in DEMO_DOCS:
            if any(matches(s, d) for s in sinonimos):
                candidatos.append(d)
    return candidatos, notas

def score_hibrido(doc: Dict[str, Any], hoy: date) -> float:
    rel = float(doc.get("relevancia", 0.0))  # 0..1
    d = to_date(doc["fecha"])
    if not d:
        recency = 0.5
    else:
        days = (hoy - d).days
        tau = 540.0  # ~18 meses
        recency = math.exp(-max(0, days) / tau)  # 1 hoy; ~0.37 a ~18m
    return 0.7 * rel + 0.3 * recency

def sort_docs(docs: List[Dict[str, Any]], orden: str, hoy: date) -> List[Dict[str, Any]]:
    if orden == "relevancia_desc":
        return sorted(docs, key=lambda d: d.get("relevancia", 0.0), reverse=True)
    if orden == "fecha_desc":
        return sorted(docs, key=lambda d: d.get("fecha", ""), reverse=True)
    # por defecto híbrido_desc
    return sorted(docs, key=lambda d: score_hibrido(d, hoy), reverse=True)

def filtra_docs(docs: List[Dict[str, Any]], desde: Optional[str], hasta: Optional[str], organo: Optional[str]):
    notas: List[str] = []
    d1 = to_date(desde) if desde else None
    d2 = to_date(hasta) if hasta else None
    if d1 and d2 and d1 > d2:
        d1, d2 = d2, d1
        notas.append("Se corrigió el rango de fechas (invertido).")
    org_norm = normaliza_organo(organo)
    if organo and org_norm != organo:
        notas.append(f"Órgano normalizado a “{org_norm}”.")
    out = []
    for doc in docs:
        ok = True
        if org_norm and doc["organo"] != org_norm:
            ok = False
        if d1 and to_date(doc["fecha"]) < d1:
            ok = False
        if d2 and to_date(doc["fecha"]) > d2:
            ok = False
        if ok:
            out.append(doc)
    return out, notas, org_norm

# ---------------------------
# Endpoints
# ---------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.16"}

@app.get("/buscar-cendoj", response_model=Respuesta)
def buscar_cendoj(
    query: str = Query(..., description="Consulta de texto libre"),
    organo: Optional[str] = Query(None, description="Órgano: TS, TSJC, AN..."),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Optional[str] = Query("hibrido_desc", description="hibrido_desc | relevancia_desc | fecha_desc"),
    limite: Optional[int] = Query(5, ge=1, le=50, description="Nº máximo de resultados"),
    validar_enlaces: Optional[bool] = Query(False, description="Validar el enlace directo contra CENDOJ"),
):
    # Limpieza simple de la query
    q_limpia = re.sub(r"[“”\"']", "", query or "").strip()
    if not q_limpia:
        raise HTTPException(status_code=400, detail="La consulta no puede estar vacía.")

    # Expansión de sinónimos
    q_base, sinonimos_usados = expand_query(q_limpia)

    # Búsqueda base (sobre DEMO)
    docs, notas_busqueda = buscar_base(q_base, sinonimos_usados)

    # Filtros
    try:
        docs, notas_filtros, org_norm = filtra_docs(docs, desde, hasta, organo)
    except ValueError:
        raise HTTPException(status_code=400, detail="Rango de fechas inválido. Usa YYYY-MM-DD.")

    # Ordenación
    docs = sort_docs(docs, orden or "hibrido_desc", today())

    # Límite
    docs = docs[: (limite or 5)]

    resultados: List[Resultado] = []
    notas: List[str] = []
    notas.extend(notas_busqueda)
    notas.extend(notas_filtros)

    for d in docs:
        url_dir = enlace_directo(d["id_cendoj"])
        url_est = enlace_estable(d.get("roj"), d.get("ecli"))

        estrategia = "directo"
        preferido = url_dir
        ok_dir: Optional[bool] = None

        if validar_enlaces:
            ok_dir = valida_directo_stdlib(url_dir)
            if ok_dir is False:
                estrategia = "estable"
                preferido = url_est
                notas.append(f"El enlace directo de {d['id_cendoj']} no respondió como esperado; se usa enlace estable (ECLI/ROJ).")

        resumen = make_resumen(d["titulo"], d["organo"], d.get("sala"), d["fecha"])

        resultados.append(Resultado(
            titulo=d["titulo"],
            organo=d["organo"],
            sala=d.get("sala"),
            ponente=d.get("ponente"),
            fecha=d["fecha"],
            relevancia=pct_int(d.get("relevancia", 0.0)),
            resumen=resumen,
            id_cendoj=d.get("id_cendoj"),
            roj=d.get("roj"),
            ecli=d.get("ecli"),
            url_directo=url_dir,
            url_estable=url_est,
            enlace_preferido=preferido,
            enlace_directo_ok=ok_dir,
            estrategia_enlace=estrategia,
        ))

    # Nota final
    if not resultados:
        nota_final = "No se han encontrado resultados exactos. Prueba con sinónimos o ajusta el rango de fechas."
    else:
        extras = []
        if sinonimos_usados:
            extras.append("sinónimos: " + ", ".join(sinonimos_usados))
        if (orden or "hibrido_desc") == "hibrido_desc":
            extras.append("orden: ranking híbrido (relevancia + actualidad)")
        if extras:
            notas.append("Info: " + "; ".join(extras))
        nota_final = "; ".join(notas) if notas else None

    return Respuesta(
        query=q_limpia,
        total=len(resultados),
        resultados=resultados,
        nota=nota_final
    )