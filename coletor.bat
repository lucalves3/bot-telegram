@echo off
rem Coletor: busca a Amazon por esta internet e manda as ofertas para o bot no Railway.
rem Deixe esta janela aberta (pode minimizar). Para parar, feche a janela.
title Coletor de ofertas
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
:loop
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" collector.py
echo Coletor parou; reiniciando em 30 s...
timeout /t 30 /nobreak >nul
goto loop
