# Informes PSE → PKE (canal de comunicación obligatorio)

Esta carpeta es el **único canal de comunicación formal** entre el Project
Software Engineer (PSE) y el Project Knowledge Engineer (PKE).

## Por qué existe esta carpeta

El PSE trabaja en sesiones de chat efímeras. El PKE, por diseño (`Agente_PKE.md`),
**nunca utiliza recuerdos de conversaciones anteriores** y solo confía en el
código y en la Base de Conocimiento ubicada en `/ai`. Sin un artefacto físico
persistido en disco, un informe entregado únicamente como texto de chat es
**invisible para el PKE** y se pierde en cuanto termina la sesión del PSE.

Esta carpeta cierra esa brecha: convierte el "Informe de Cambios" que el PSE
ya está obligado a generar (ver `Ingenieria_Segura_PSE.md`) en un documento
real dentro de `/ai`, que el PKE puede — y debe — leer.

## Protocolo

### Escritura (responsabilidad del PSE)

- Al finalizar **cualquier** tarea que haya creado, modificado o eliminado
  código fuente, el PSE debe crear un archivo nuevo en esta carpeta (nunca
  sobrescribir uno existente) con el nombre:

  ```
  AAAA-MM-DD-slug-descriptivo.md
  ```

- El archivo debe seguir la plantilla de `PLANTILLA.md` en esta misma carpeta.
- La tarea del PSE **no se considera finalizada** hasta que este archivo
  exista físicamente en el repositorio.

### Lectura (responsabilidad del PKE)

- Al iniciar **cualquier** tarea, antes de ejecutar lo que el usuario haya
  pedido, el PKE debe listar esta carpeta (excluyendo `procesados/`).
- Si existen informes pendientes, debe procesarlos primero (incorporar su
  contenido a la Base de Conocimiento siguiendo su flujo normal PASO 1-10),
  y luego mover cada archivo procesado a `ai/informes_pse/procesados/`
  (sin modificar su contenido — solo se mueve, como evidencia de auditoría).
- Esto aplica incluso si el usuario no menciona los informes explícitamente.

## Estado

- `ai/informes_pse/*.md` → informes **pendientes** de procesar.
- `ai/informes_pse/procesados/*.md` → informes ya incorporados a la Base de
  Conocimiento (archivo histórico, no se vuelve a leer salvo auditoría).
- `ai/informes_pse/LOG_PASO0.md` → registro auditable, con una línea por cada
  ejecución del PKE (haya o no informes pendientes). Es el mecanismo objetivo
  para comprobar que el PASO 0 se ejecuta siempre: si el PKE corrió y no hay
  línea nueva ahí, el PASO 0 no se ejecutó. Ver `metodologia_pke.md` (sección
  "Ingesta de Informes del PSE", sub-paso 6) para el detalle del protocolo.

## Por qué esta carpeta sola no bastó

Esta carpeta y el PASO 0 ya existían (desde la versión 1.2 de la
metodología) cuando se detectó que, aun así, dos informes quedaron sin
procesar durante varias tareas posteriores del PKE. La causa no fue la
ausencia del mecanismo, sino que era una instrucción en texto sin ningún
artefacto que probara su cumplimiento turno a turno — nada impedía que el
PKE simplemente no la ejecutara sin que nadie lo notara hasta la próxima
auditoría manual. `LOG_PASO0.md` cierra esa brecha: convierte el
cumplimiento del PASO 0 en un hecho verificable en el repositorio, no en una
promesa.
