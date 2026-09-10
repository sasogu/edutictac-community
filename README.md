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
| `edutictac_community.migrations` | `apply_migrations(...)` — versionat SQLite per namespace |
| `edutictac_community.ratelimit` | `RateLimiter(max_calls, window_seconds)` |
| `edutictac_community.session` | `SignedSession` — cookies firmades HMAC |
| `edutictac_community.oidc` | `OIDCClient` — authorization code + PKCE (authlib) |
| `edutictac_community.community` | `create_community_router(...)` — favorits/valoracions/avisos |

## Consumidors en producció

| Servei | Ús actual del nucli | Estat |
|---|---|---|
| `recursos-api` | SQLite, rate limit, cookies firmades, OIDC i router de comunitat amb `game_key` | Producció (`v0.1.4`) |
| `edumusic-api` | SQLite i rate limit | Producció (`v0.1.1`) |
| `edutictac-id-api` | SQLite, rate limit i cookies firmades | Producció (`v0.1.1`) |

El router compartit usa `item_key` per defecte, però accepta `key_field` i
`db_key_column` per adaptar-se a contractes existents. `recursos-api` l'usa amb
`game_key` tant en el JSON públic com en les taules SQLite, sense migrar dades
ni canviar la PWA. També es pot configurar `admin_hide_path` per preservar rutes
existents com `/api/admin/resources/hide`.

## Instal·lació

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Per consumir una versió estable des d'un backend EduTicTac:

```txt
edutictac-community @ git+https://git.edutictac.es/Edutictac/edutictac-community.git@v0.1.4
```

## Migracions

El paquet usa una taula pròpia `_edutictac_migrations` amb versionat per
`namespace`, no `PRAGMA user_version`. Això evita conflictes quan una mateixa
SQLite conté taules de l'aplicació i taules compartides del nucli.

Exemple:

```python
from edutictac_community.migrations import Migration, apply_migrations

with connect(db_path) as conn:
    apply_migrations(
        conn,
        "community:game_key",
        [Migration(1, "... SQL idempotent ...")],
    )
```

El router de comunitat ja aplica automàticament la migració inicial del seu
esquema (`community:item_key` o `community:game_key`, segons configuració).

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

La suite usa `httpx.ASGITransport` per provar routers FastAPI sense dependre de
`fastapi.testclient.TestClient`, que en algunes combinacions recents de
FastAPI/Starlette/httpx pot bloquejar-se.

## Notes d'operació

- El paquet no guarda estat compartit entre aplicacions: cada servei conserva la
  seua base de dades i el seu desplegament.
- `db.connect()` activa WAL i `sqlite3.Row`; si una app necessita més pragmes
  locals, com `PRAGMA foreign_keys=ON`, els aplica després d'obrir connexió.
- `SignedSession` només resol signatura i atributs de cookie. La semàntica de
  sessió continua sent responsabilitat de cada servei.
- `RateLimiter` és en memòria; és suficient per als serveis actuals d'un sol
  procés. Si un servei escala a diversos workers o nodes, caldrà un backend
  compartit.

## Llicència

MIT.
