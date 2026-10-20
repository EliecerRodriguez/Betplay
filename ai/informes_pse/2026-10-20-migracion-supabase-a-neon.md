# Informe PSE → PKE — Migración de Supabase a Neon

**Fecha:** 2026-10-20
**Tarea solicitada por el usuario:** Migrar la base de datos de Supabase (plan gratuito, se borró) a Neon PostgreSQL, manteniendo la funcionalidad de NBA y ATP sin cambios.

## 1. Objetivo del cambio

Reemplazar Supabase (PostgreSQL) como backend de persistencia por **Neon PostgreSQL serverless**, porque la instancia gratuita de Supabase se eliminó sin previo aviso y el usuario perdió la base de datos. La migración debía ser transparente para NBA y ATP (sin cambios de funcionalidad), manteniendo GitHub Actions como orquestador y arrancando desde cero (sin migración de datos).

## 2. Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `config/settings.py` | Comentarios de "Supabase" → "Neon". Lógica de `DATABASE_URL` intacta (los defaults ya apuntaban a puerto 5432). |
| `.env` | `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` y `DATABASE_URL` actualizados a la conexión pooled de Neon. Se omitió `channel_binding=require` por compatibilidad con `psycopg2-binary`; se conservó `sslmode=require` (obligatorio en Neon). |
| `database/repository.py` | Docstring y log de "Supabase" → "Neon". **Bug corregido:** la vista `v_backtest_history` en `_SETUP_SQL` usaba columnas inexistentes `p.home_team`/`p.away_team`; ahora usa `p.home_team_id`/`p.visitor_team_id` (columnas reales del modelo `Prediction`). Esto hacía que la vista NUNCA se creara automáticamente en el arranque. |
| `sports/nba/database/repository.py` | Mismo cambio que `database/repository.py` (repo duplicado para NBA). |
| `run_pipeline.py` | Función `save_to_supabase()` renombrada a `save_to_database()` + docstring/log actualizados. La función ya no menciona Supabase. |
| `web/app.py` | Comentarios, docstrings y logs de "Supabase" → "Neon". Variable interna `supabase_results` renombrada a `db_results`. El valor `"source": "supabase"` del endpoint `/api/accuracy` cambió a `"source": "neon"`. |
| `.env.example` | **Antes estaba vacío.** Ahora poblado con plantilla Neon (host, puerto 5432, URL de ejemplo, resto de config). |

## 3. Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `database/neon_setup.sql` | Script de setup para Neon (esquema + índices + vistas). **Sin** `pg_cron`/`pg_net` (extensiones exclusivas de Supabase). La orquestación diaria la cubre GitHub Actions. |

## 4. Archivos eliminados

Ninguno. Los `supabase_setup.sql` (duplicados en `database/` y `sports/nba/database/`) se **conservaron** como referencia histórica (contienen la parte SQL estándar que ahora vive en `neon_setup.sql`). Se recomienda eliminarlos en un paso posterior de limpieza (ver Riesgos).

## 5. Funcionalidades agregadas

- Soporte de base de datos Neon PostgreSQL (mismo esquema, puerto 5432).
- `.env.example` funcional (antes vacío) para reproducibilidad.
- Script `neon_setup.sql` documentado para inicialización manual.

## 6. Funcionalidades eliminadas

- Dependencia de `pg_cron`/`pg_net` (jobs automáticos de Supabase). **No se pierde funcionalidad**: la orquestación real ya la hacía GitHub Actions (`reconcile.yml`, `daily_pipeline.yml`, `atp_pipeline.yml`, `catchup.yml`), no pg_cron.

## 7. Cambios arquitectónicos

- **Backend de persistencia:** Supabase (Transaction Pooler, puerto 6543) → Neon PostgreSQL serverless (puerto 5432, `sslmode=require`).
- La arquitectura por capas, los modelos ORM (SQLAlchemy), los upserts (`INSERT ... ON CONFLICT`) y las vistas (`v_model_accuracy`, `v_backtest_history`) **no cambian** — son SQL estándar y funcionan idéntico en Neon.
- La cadena de conexión viaja por `DATABASE_URL` (con precedencia sobre campos individuales), igual que antes.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `database.md` — describe Supabase/puerto 6543; debe actualizarse a Neon/puerto 5432.
- `config.md` — muestra `.env` con host Supabase; debe actualizarse.
- `github_actions.md` — secreto `DATABASE_URL` ahora apunta a Neon; el formato de URL cambió.
- `web_dashboard.md` — menciona SQLAlchemy (Supabase); referencias a actualizar.
- `pipeline_nba.md` — menciona "upsert a Supabase".
- `pipeline_atp.md` — menciona "no usa Supabase"; sigue siendo cierto (ATP solo CSV), pero la referencia al proveedor debería ser "Neon".
- `arquitectura.md` — tabla de stack: "PostgreSQL (Supabase) puerto 6543" → "PostgreSQL (Neon) puerto 5432".
- `ai/README.md` — mención de Supabase en la descripción del sistema.

## 9. Riesgos detectados

- 🔴 **Bug preexistente corregido:** la vista `v_backtest_history` no se creaba automáticamente porque usaba columnas inexistentes (`p.home_team`/`p.away_team`). En Supabase la vista real se creaba vía `supabase_setup.sql` (con la versión correcta por team_id), por eso nunca se notó. Ahora ambos repos usan las columnas correctas. **Esto es un cambio de comportamiento positivo**, pero el PKE debe conocerlo.
- 🟡 **`channel_binding=require` omitido de la URL:** se descartó para compatibilidad con `psycopg2-binary`. La conexión verificada funciona sin él. Si Neon llegara a requerirlo en el futuro, habría que habilitarlo.
- 🟡 **Credenciales compartidas en chat:** el usuario compartió el password de Neon y claves S3/AI Gateway en la conversación. Recomendación de seguridad: rotar el password de Neon si el chat no es privado.
- 🟡 **`.env` no versionado:** la nueva URL vive solo en `.env` local y en el secreto de GitHub. Si se pierde el archivo, hay que reconfigurarlo manualmente (mitigado por el `.env.example` ahora poblado).
- 🟢 **Scripts `supabase_setup.sql` obsoletos:** siguen en el repo (con sección pg_cron inaplicable). No rompen nada, pero conviene eliminarlos o renombrarlos a `neon_setup.sql` en limpieza futura.
- 🟢 **ATP sigue sin persistencia en BD:** el pipeline ATP solo escribe CSVs. Es una decisión de diseño previa, NO se modificó en esta migración.

## 10. Recomendaciones de actualización para la Base de Conocimiento

1. **`ai/arquitectura.md`:** cambiar fila "Base de datos | PostgreSQL (Supabase) | Puerto 6543 (Transaction Pooler)" → "PostgreSQL (Neon) | Puerto 5432 (sslmode=require)".
2. **`ai/modulos/database.md`:** reemplazar todas las referencias a "Supabase" y "puerto 6543" por "Neon" y "puerto 5432". Documentar el nuevo `neon_setup.sql`. Documentar el bug de la vista `v_backtest_history` corregido.
3. **`ai/modulos/config.md`:** actualizar el ejemplo de `.env` con host de Neon y la URL de ejemplo.
4. **`ai/modulos/github_actions.md`:** actualizar el formato del secreto `DATABASE_URL` (ahora Neon con `?sslmode=require`).
5. **`ai/modulos/web_dashboard.md` / `pipeline_nba.md` / `pipeline_atp.md`:** reemplazar menciones a "Supabase" por "Neon" (manteniendo la nota de que ATP no persiste en BD).
6. **`ai/README.md`:** actualizar la descripción del sistema y la tabla de stack (Supabase → Neon).

---
**Nota de verificación (PSE):** La migración se verificó de punta a punta contra Neon real:
- Conexión con SQLAlchemy: OK (PostgreSQL 18.6).
- `Base.metadata.create_all`: creó 7 tablas correctamente.
- Vistas `v_model_accuracy` y `v_backtest_history`: creadas correctamente tras la corrección del bug.
- Upsert real (`upsert_teams`): 1 fila escrita y releída desde Neon OK.
- Quedó **pendiente de acción del usuario**: actualizar el secreto `DATABASE_URL` en GitHub Actions Settings (ver sección 8/9). El PKE debe anotar este paso como pendiente operativo hasta que el usuario confirme.
