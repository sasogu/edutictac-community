# EduTicTac Community

Nucli compartit per als microserveis del **Commons EduTicTac**. No centralitza
dades: és una llibreria que cada aplicació instal·la per reutilitzar els blocs
repetits en tots els backends (SQLite, rate limiting, cookies firmades, OIDC i
la lògica de comunitat de favorits/valoracions/avisos).

## Motivació

Els backends propis (`recursos-api`, `edumusic-api`, `edutictac-id-api`, …)
repeteixen el mateix patró: connexió SQLite amb WAL, limitació de peticions en
memòria, cookies de sessió firmades amb HMAC, integració OIDC amb Authentik i
taules de favorits/valoracions/avisos. Aquesta llibreria consolida eixe patró
perquè cada app nova no el torne a escriure, sense convertir-los en un sol
servei monolític.

## Decisió de disseny: federada amb nucli comú

La identitat (`edutictac-id-api`) és l'únic servei central i estable, i
l'itinerari/recorregut també viu centralitzat perquè travessa apps. Però la
lògica de domini (favorits/valoracions/puntuacions) **no es centralitza**: cada
app manté el seu backend propi i usa aquesta llibreria per a les parts comunes.
Vegeu `DECISIONS-DISSENY.md` al repositori `edutictac-commons`.

## Mòduls

| Mòdul | Funció |
|---|---|
| `edutictac_community.db` | `connect(db_path)` — SQLite amb WAL i Row factory |
| `edutictac_community.ratelimit` | `RateLimiter(max_calls, window_seconds)` |
| `edutictac_community.session` | `SignedSession` — cookies firmades HMAC |
| `edutictac_community.oidc` | `OIDCClient` — authorization code + PKCE (authlib) |
| `edutictac_community.community` | `create_community_router(...)` — favorits/valoracions/avisos |

## Instal·lació

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Exemple

`examples/minimal_app.py` munta el router de comunitat amb una identitat
anònima per cookie:

```bash
uvicorn examples.minimal_app:app --host 127.0.0.1 --port 8010
```

## Tests

```bash
pytest -q
```

La suite usa `httpx.ASGITransport` para probar routers FastAPI sin depender de
`fastapi.testclient.TestClient`, que en algunas combinaciones recientes de
FastAPI/Starlette/httpx puede bloquearse.

## Llicència

MIT.
