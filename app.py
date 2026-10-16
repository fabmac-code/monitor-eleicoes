"""
Monitor de apuração — 2º turno presidencial 2026.

  streamlit run app.py

O coletor roda em segundo plano dentro do próprio app (1 thread por modo) e
grava em SQLite; a tela relê o banco a cada 20 s.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import importlib

import analise

# recarrega o módulo de cálculo a cada execução completa: evita usar uma versão antiga
# que ficou na memória quando os arquivos são atualizados com o app aberto
analise = importlib.reload(analise)
from coletor import Coletor, PASTA

st.set_page_config(page_title="Apuração 2º Turno 2026", layout="wide", initial_sidebar_state="expanded")
st.markdown("<style>.block-container{padding-top:3.5rem;padding-bottom:0;max-width:1300px}</style>",
            unsafe_allow_html=True)

MODOS = {
    "2º turno (TSE, automático)": {"modo": "2t", "eleicao": None, "base": None, "db": "apuracao.db"},
    "Teste: 1º turno 2026 (TSE)": {"modo": "teste", "eleicao": "6257", "base": None, "db": "apuracao_teste.db"},
    "Simulação local": {"modo": "sim", "eleicao": None, "base": "http://127.0.0.1:8765", "db": "apuracao_sim.db"},
}
TEMPLATE = (PASTA / "template.html").read_text(encoding="utf-8")


@st.cache_resource
def coletores() -> dict:
    return {}


def coletor_para(nome: str) -> Coletor:
    reg = coletores()
    for outro, c in list(reg.items()):  # só um coletor ativo por vez
        if outro != nome:
            c.parar()
            del reg[outro]
    if nome not in reg:
        cfg = MODOS[nome]
        c = Coletor(cfg["eleicao"], db=PASTA / cfg["db"], base=cfg["base"],
                    turno=2 if cfg["modo"] in ("2t", "sim") else None)
        c.iniciar_em_thread()
        reg[nome] = c
    return reg[nome]


def simulador_ativo() -> bool:
    with socket.socket() as so:
        so.settimeout(0.5)
        return so.connect_ex(("127.0.0.1", 8765)) == 0


@st.cache_resource
def processo_simulador() -> dict:
    return {"p": None}


def iniciar_simulador(reiniciar: bool = False):
    reg = processo_simulador()
    if reiniciar and reg["p"] is not None:
        reg["p"].terminate()
        reg["p"].wait(timeout=5)
        reg["p"] = None
    if not simulador_ativo():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        reg["p"] = subprocess.Popen([sys.executable, str(PASTA / "simulador_tse.py"), "--minutos", "15"],
                                    cwd=PASTA, creationflags=flags)
        for _ in range(50):  # espera o simulador abrir a porta antes da 1ª coleta
            if simulador_ativo():
                break
            time.sleep(0.1)


with st.sidebar:
    st.header("Monitor")
    nome = st.radio("Fonte de dados", list(MODOS), index=0)
    ano = st.radio("Comparar com", analise.ANOS, index=1, horizontal=True,
                   format_func=lambda a: "1º turno 2026" if a == "1T 2026" else f"2º turno {a}")
    if MODOS[nome]["modo"] == "sim":
        reinicia = st.button("Reiniciar simulação (do zero)")
        iniciar_simulador(reiniciar=reinicia)
        if reinicia:
            for c in coletores().values():
                c.parar()
                c.con.close()
            coletores().clear()
            try:
                (PASTA / "apuracao_sim.db").unlink(missing_ok=True)
            except OSError:
                pass  # se o Windows segurar o arquivo, a série antiga continua visível até reabrir o app
        st.caption("Simulador local iniciado automaticamente · apuração completa em 15 min · não são dados reais")
    st.divider()
    auto = st.toggle("Atualizar a tela automaticamente", value=True,
                     help="Desligado: a coleta continua a cada 60 s, mas a tela só muda quando você clicar em Atualizar tela.")
    buscar = st.button("Buscar no TSE agora", use_container_width=True)
    st.caption(f"Bases de comparação carregadas: {len(analise.ANOS)}")
    st.caption("Coleta automática a cada 60 s (arquivos .jws do TSE). A tela confere a cada 20 s se há dado novo.")

col = coletor_para(nome)
if buscar:
    col.buscar_agora()
    st.toast("Busca no TSE disparada. O resultado aparece em alguns segundos.")


@st.fragment(run_every=20)
def painel():
    con = col.con
    status = dict(con.execute("SELECT chave, valor FROM status").fetchall())
    eleicao = col.eleicao  # só o que este coletor detectou (não reaproveita sessões antigas do banco)
    if not eleicao:
        st.info("Aguardando a primeira coleta…" + (f"  \nErro: {status['erros']}" if status.get("erros") else ""))
        return
    n, ultimo = con.execute("SELECT COUNT(*), MAX(coleta) FROM leituras WHERE eleicao=?", (eleicao,)).fetchone()
    if n == 0:
        st.info(f"Eleição {eleicao}: arquivos ainda não publicados pelo TSE (a divulgação começa às 17h). "
                f"Tentando de novo a cada 60 s." + (f"  \nÚltimo erro: {status['erros']}" if status.get("erros") else ""))
        return
    # Só recalcula e redesenha quando chegam dados novos (ou muda o ano/erro). Com o mesmo HTML,
    # o navegador mantém o painel como está: sem piscar, sem perder a posição de rolagem.
    assinatura = (nome, eleicao, ano, n, ultimo, status.get("erros", ""), (PASTA / "template.html").stat().st_mtime)
    cache = st.session_state.get("painel_cache")
    mesma_base = cache is not None and cache[0][:3] == assinatura[:3]  # mesma fonte/eleição/ano
    pediu = st.session_state.pop("forcar_tela", False)
    if cache and cache[0] != assinatura and mesma_base and not auto and not pediu:
        br = con.execute("SELECT pst FROM leituras WHERE eleicao=? AND uf='BR' ORDER BY coleta DESC LIMIT 1",
                         (eleicao,)).fetchone()
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        c1.warning(f"Dados novos disponíveis ({(ultimo or '')[11:16]}"
                   f"{f' · {br[0]:.1f}% apurado' if br else ''}). A tela abaixo é a da última atualização.")
        if c2.button("Atualizar tela", type="primary", use_container_width=True):
            st.session_state["forcar_tela"] = True
            st.rerun(scope="fragment")
    elif not cache or cache[0] != assinatura:
        dados = analise.montar_payload(con, eleicao, {**status, "ultima_rodada": ultimo})
        dados["ano"], dados["modo"] = ano, MODOS[nome]["modo"]
        html = (PASTA / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(dados, ensure_ascii=False, default=float))
        st.session_state["painel_cache"] = cache = (assinatura, html)
    components.html(cache[1], height=6400, scrolling=True)


painel()
