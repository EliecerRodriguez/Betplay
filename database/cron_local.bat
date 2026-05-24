@echo off
REM =============================================================================
REM Betplay NBA — Cron local de Windows
REM =============================================================================
REM Tareas registradas (ejecutar como administrador, una sola vez):
REM
REM   -- Eliminar tareas anteriores (si existen) --
REM   schtasks /delete /tn "Betplay Pipeline"  /f
REM   schtasks /delete /tn "Betplay Reconcile" /f
REM
REM   -- Crear tareas nuevas --
REM   schtasks /create /tn "Betplay Reconcile" /tr "cmd /c cd /d C:\Betplay && python reconcile.py >> logs\cron.log 2>&1"   /sc daily /st 06:00 /ru SYSTEM /f
REM   schtasks /create /tn "Betplay Pipeline"  /tr "cmd /c cd /d C:\Betplay && python run_pipeline.py >> logs\cron.log 2>&1" /sc daily /st 06:30 /ru SYSTEM /f
REM =============================================================================

SET BETPLAY_DIR=C:\Betplay
SET LOG_DIR=%BETPLAY_DIR%\logs
SET PYTHON=C:\Betplay\.venv\Scripts\python.exe
SET PYTHONPATH=%BETPLAY_DIR%

REM Determinar la hora actual para elegir la tarea (uso manual del bat)
FOR /F "tokens=1 delims=:" %%H IN ("%TIME%") DO SET HOUR=%%H
FOR /F "tokens=2 delims=:" %%M IN ("%TIME%") DO SET MIN=%%M
SET HOUR=%HOUR: =%
SET MIN=%MIN: =%

cd /d %BETPLAY_DIR%

IF %HOUR% EQU 6 IF %MIN% LSS 15 (
    REM 06:00 — Reconciliar resultados de ayer
    echo [%DATE% %TIME%] Reconciliando resultados... >> %LOG_DIR%\cron.log 2>&1
    %PYTHON% reconcile.py >> %LOG_DIR%\cron.log 2>&1
    GOTO END
)

REM 06:30 — Ejecutar pipeline de predicciones del dia
echo [%DATE% %TIME%] Ejecutando pipeline... >> %LOG_DIR%\cron.log 2>&1
%PYTHON% run_pipeline.py >> %LOG_DIR%\cron.log 2>&1

:END
echo [%DATE% %TIME%] Tarea completada. >> %LOG_DIR%\cron.log 2>&1
