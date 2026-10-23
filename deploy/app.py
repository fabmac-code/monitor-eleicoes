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
from coletor import Coletor, PASTA, TZ

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


MODOS = {"2t": {"modo": "2t", "eleicao": None, "base": None, "db": "apuracao.db",
               "titulo": "2º turno 2026", "tag": ":green-badge[AO VIVO]",
               "desc": "Dados oficiais do TSE, coletados automaticamente."}}
if config("MOSTRAR_TESTE", "1") == "1":
    MODOS["teste"] = {"modo": "teste", "eleicao": "6257", "base": None, "db": "apuracao_teste.db",
                      "titulo": "1º turno 2026", "tag": ":gray-badge[TESTE]",
                      "desc": "Resultado final do TSE. Serve para testar a tela."}
BASES = {"1T 2026": ("2026", "1º turno"), "2022": ("2022", "2º turno"),
         "2018": ("2018", "2º turno"), "2014": ("2014", "2º turno")}
CSS_BARRA = """<style>
section[data-testid="stSidebar"] h1{font-size:1.6rem;margin-bottom:.2rem}
.passo{font-size:.74rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#7c8882;margin:1.1rem 0 .35rem}
/* 1 · fontes como cartões (DOM do Streamlit >= 1.4x: radiogroup > div[data-selected] > label) */
.st-key-fonte [role="radiogroup"]{gap:8px;width:100%}
.st-key-fonte [role="radiogroup"] > div, .st-key-fonte [role="radiogroup"] > label{border:1px solid #dfe3df;border-radius:10px;
  padding:10px 12px;margin:0;background:#fff;width:100%;box-sizing:border-box}
.st-key-fonte [role="radiogroup"] > div[data-selected="true"], .st-key-fonte [role="radiogroup"] > label:has(input:checked){
  border-color:#e5484d;box-shadow:0 0 0 1px #e5484d}
/* 2 · bases em grade 2×2, sem a bolinha do rádio */
.st-key-base, .st-key-base [data-testid="stRadio"], .st-key-base [data-testid="stRadio"] > div{width:100%!important}
.st-key-base [role="radiogroup"]{display:grid!important;grid-template-columns:1fr 1fr;gap:8px;width:100%}
.st-key-base [role="radiogroup"] > div{width:100%}
.st-key-base [role="radiogroup"] > div, .st-key-base [role="radiogroup"] > label{border:1px solid #dfe3df;border-radius:8px;
  padding:8px 4px;margin:0;background:#fff;box-sizing:border-box;cursor:pointer}
.st-key-base [data-testid="stRadioOption"], .st-key-base [data-testid="stRadioOption"] > div{width:100%;justify-content:center}
.st-key-base [data-testid="stRadioOption"] > div > div:first-child{display:none}
.st-key-base p{text-align:center;line-height:1.25;margin:0}
.st-key-base [role="radiogroup"] > div[data-selected="true"], .st-key-base [role="radiogroup"] > label:has(input:checked){
  background:#e5484d;border-color:#e5484d}
.st-key-base [role="radiogroup"] > div[data-selected="true"] p, .st-key-base [role="radiogroup"] > label:has(input:checked) p{color:#fff}
</style>"""


def contador_html(ultima: str | None, intervalo: int = 60) -> str:
    """Contagem regressiva até a próxima coleta (roda no navegador; ressincroniza a cada atualização)."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ultima) if ultima else None
        agora = datetime.now(TZ).replace(tzinfo=None)  # servidor em UTC; a coleta grava horário de Brasília
        resta = max(0, intervalo - int((agora - dt).total_seconds())) if dt else intervalo
        hora = dt.strftime("%H:%M:%S") if dt else "aguardando"
    except ValueError:
        resta, hora = intervalo, "—"
    return f"""<div style="font-family:'Source Sans Pro',system-ui,sans-serif;font-size:12.5px;color:#31333f">
<div style="display:flex;justify-content:space-between;align-items:baseline">
<b id="t" style="white-space:nowrap">Próxima em {resta} s</b><span style="color:#7c8882;white-space:nowrap">última {hora}</span></div>
<div style="height:4px;background:#eceeeb;border-radius:2px;margin-top:6px;overflow:hidden">
<div id="b" style="height:100%;width:{100*resta/intervalo:.0f}%;background:#e5484d;transition:width 1s linear"></div></div></div>
<script>let r={resta},T={intervalo};setInterval(()=>{{r=r>0?r-1:T;
document.getElementById('t').textContent=r>0?'Próxima em '+r+' s':'Coletando…';
document.getElementById('b').style.width=(100*r/T)+'%';}},1000);</script>"""
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
    st.markdown(CSS_BARRA, unsafe_allow_html=True)
    st.title("Monitor")

    st.markdown('<div class="passo">1 · O que acompanhar</div>', unsafe_allow_html=True)
    nome = st.radio("O que acompanhar", list(MODOS), key="fonte", label_visibility="collapsed",
                    format_func=lambda k: f"**{MODOS[k]['titulo']}** {MODOS[k]['tag']}",
                    captions=[MODOS[k]["desc"] for k in MODOS])

    st.markdown('<div class="passo">2 · Comparar com</div>', unsafe_allow_html=True)
    # comparar o 1º turno com ele mesmo não faz sentido: a opção some quando a fonte é o 1º turno
    opcoes = [a for a in analise.ANOS if not (MODOS[nome]["modo"] == "teste" and a == "1T 2026")]
    if st.session_state.get("base") not in opcoes:
        st.session_state["base"] = "2022"
    ano = st.radio("Comparar com", opcoes, key="base", horizontal=True, label_visibility="collapsed",
                   format_func=lambda a: f"**{BASES.get(a, (a, ''))[0]}**  \n{BASES.get(a, (a, ''))[1]}")
    st.caption("Δ = parcial de 2026 − resultado final da base escolhida.")
    if len(analise.ANOS) < 4:
        st.warning(f"Só {len(analise.ANOS)} de 4 bases carregadas. Reinicie o app (menu ⋮ > Reboot app).")

    col = coletor_para(nome)
    st.markdown('<div class="passo">3 · Atualização</div>', unsafe_allow_html=True)
    with st.container(border=True):
        auto = st.toggle("Atualizar automaticamente", value=True, key="auto")
        ultima = dict(col.con.execute("SELECT chave, valor FROM status").fetchall()).get("ultima_rodada")
        st.iframe(contador_html(ultima), height=46)
        buscar = st.button("Buscar agora", use_container_width=True, type="secondary" if auto else "primary")
    with st.expander("Como funciona a coleta"):
        st.caption("O app baixa do TSE, a cada 60 s, os 29 arquivos assinados (.jws) de presidente: 27 UFs, "
                   "exterior e Brasil. Só grava quando os números mudam. A tela confere a cada 20 s se há dado novo "
                   "e só é redesenhada quando há. Com \"Atualizar automaticamente\" desligado, a coleta continua, mas a "
                   "tela só muda quando você clicar em Atualizar tela. \"Buscar agora\" antecipa a próxima consulta "
                   "(no máximo uma vez a cada 30 s, somando todos os visitantes).")
        st.caption("Versão online: se o app reiniciar, a curva da noite recomeça do zero; os números atuais são "
                   "relidos do TSE na hora.")

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
