@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
start "Simulador TSE" python simulador_tse.py --minutos 15
python -m streamlit run app.py --server.port 8510
