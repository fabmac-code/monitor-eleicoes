"""
Monitor de apuração — 2º turno presidencial 2026 · VERSÃO PARA DEPLOY (Streamlit Community Cloud).

Diferenças em relação à versão local:
- sem o modo "Simulação local" (não faz sentido num app público);
- os coletores dos modos disponíveis rodam em paralelo e são compartilhados por todos os
  visitantes (um visitante não troca a fonte de dados dos outros);
- "Buscar no TSE agora" tem intervalo mínimo de 30 s entre disparos, valendo para todos;
- o modo Teste (1º turno) pode ser escondido com o secret/variável MOSTRAR_TESTE = "0".
"""
from __future__ import annotations

import json
import os
import time

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

def config(nome: str, padrao: str) -> str:
    """Lê de st.secrets (painel do Streamlit Cloud) ou de variável de ambiente."""
    try:
        if nome in st.secrets:
            return str(st.secrets[nome])
    except Exception:  # noqa: BLE001 — sem secrets.toml local
        pass
    return os.environ.get(nome, padrao)


MODOS = {"2º turno (TSE, automático)": {"modo": "2t", "eleicao": None, "base": None, "db": "apuracao.db"}}
if config("MOSTRAR_TESTE", "1") == "1":
    MODOS["Teste: 1º turno 2026 (TSE)"] = {"modo": "teste", "eleicao": "6257", "base": None, "db": "apuracao_teste.db"}
TEMPLATE = (PASTA / "template.html").read_text(encoding="utf-8")


@st.cache_resource
def coletores() -> dict:
    return {}


def coletor_para(nome: str) -> Coletor:
    """Um coletor por modo, criado no primeiro acesso e mantido para todos os visitantes."""
    reg = coletores()
    if nome not in reg:
        cfg = MODOS[nome]
        c = Coletor(cfg["eleicao"], db=PASTA / cfg["db"], base=cfg["base"],
                    turno=2 if cfg["modo"] == "2t" else None)
        c.iniciar_em_thread()
        reg[nome] = c
    return reg[nome]


@st.cache_resource
def ultimo_disparo() -> dict:
    return {}


with st.sidebar:
    st.header("Monitor")
    nome = st.radio("Fonte de dados", list(MODOS), index=0)
    ano = st.radio("Comparar com", analise.ANOS, index=1, horizontal=True,
                   format_func=lambda a: "1º turno 2026" if a == "1T 2026" else f"2º turno {a}")
    st.divider()
    auto = st.toggle("Atualizar a tela automaticamente", value=True,
                     help="Desligado: a coleta continua a cada 60 s, mas a tela só muda quando você clicar em Atualizar tela.")
    buscar = st.button("Buscar no TSE agora", use_container_width=True)
    st.caption(f"Bases de comparação carregadas: {len(analise.ANOS)}")
    st.caption("Coleta automática a cada 60 s (arquivos .jws do TSE). A tela confere a cada 20 s se há dado novo.")
    st.caption("Versão online: se o app reiniciar, a curva da noite recomeça do zero; os números atuais são relidos do TSE.")

col = coletor_para(nome)
if buscar:
    disp = ultimo_disparo()
    espera = 30 - (time.time() - disp.get(nome, 0))
    if espera > 0:
        st.toast(f"Uma busca foi disparada há pouco. Tente de novo em {int(espera)} s.")
    else:
        disp[nome] = time.time()
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
    if hasattr(st, "iframe"):            # Streamlit >= 1.52: st.iframe substitui components.html
        st.iframe(cache[1], height="content")
    else:
        components.html(cache[1], height=6400, scrolling=True)


painel()
