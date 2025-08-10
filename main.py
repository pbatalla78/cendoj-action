from fastapi import FastAPI, Query

# Creamos la aplicación FastAPI
app = FastAPI(title="CENDOJ Action Mock")

@app.get("/health")
def health():
    """Endpoint de comprobación de estado."""
    return {"status": "ok"}

@app.get("/buscar-cendoj")
def buscar_cendoj(query: str = Query(..., min_length=2)):
    """
    Endpoint mock que devuelve resultados ficticios.
    En el paso siguiente conectaremos con el buscador real.
    """
    resultados = [
        {
            "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
            "ponente": "TSJC",
            "fecha": "2023-05-10",
            "url": "https://www.poderjudicial.es/search/indexAN.jsp"
        }
    ]
    return {"query": query, "total": len(resultados), "resultados": resultados}
