"""Cálculos do painel: deltas por UF, parcial bruta, projeção ajustada e série temporal."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PASTA = Path(__file__).resolve().parent
HIST_CSV = PASTA / "base_historica_2t_2014_2018_2022.csv"
T1_CSV = PASTA / "primeiro_turno_2026_uf.csv"
ANOS = ["1T 2026", "2022", "2018", "2014"]  # bases de comparação (1º turno de 2026 + 2º turnos anteriores)
T1 = "1T 2026"
MIN_PST = 0.5  # abaixo disso a UF é estimada pelo swing nacional


def carregar_bases():
    h = pd.read_csv(HIST_CSV)
    hist = {str(a): {r.uf: [int(r.aptos), int(r.comparecimento), int(r.brancos), int(r.nulos),
                             int(r.pt), int(r.adversario)] for r in g.itertuples()}
            for a, g in h.groupby("ano")}
    t1 = pd.read_csv(T1_CSV)
    pesos = dict(zip(t1.uf, t1.validos))  # válidos do 1º turno 2026 = peso de cada UF
    # 1º turno 2026 no mesmo formato: [aptos, comparecimento, brancos, nulos, Lula, Flávio]
    hist[T1] = {r.uf: [int(r.aptos), int(r.comparecimento), int(r.brancos), int(r.nulos), int(r.lula), int(r.flavio)]
                for r in t1.itertuples()}
    return hist, pesos


def outros_t1() -> dict:
    t1 = pd.read_csv(T1_CSV)
    return dict(zip(t1.uf, t1.outros.astype(int)))  # votos dos demais candidatos no 1º turno


def aptos_2026() -> dict:
    t1 = pd.read_csv(T1_CSV)
    return dict(zip(t1.uf, t1.aptos))  # eleitorado apto 2026 (o mesmo nos dois turnos)


def projetar_comparecimento(df: pd.DataFrame, hist: dict, aptos: dict) -> dict:
    """Abstenção e brancos+nulos: parcial bruta e projeção ajustada (pesos = eleitorado de cada UF).

    UF com dados: taxa observada nas seções já totalizadas. UF sem dados: taxa de 2022 + variação média
    (ponderada pelo eleitorado) das UFs já apuradas. Remove o viés da ordem de chegada (ex.: exterior
    tem ~55% de abstenção e entra por último)."""
    d = df[(df.uf != "BR") & (df.eleitores_tot > 0) & (df.pst >= MIN_PST)].copy()
    if d.empty:
        return {"abst_bruta": None, "abst_proj": None, "bn_bruta": None, "bn_proj": None}
    d["ra"] = d.abstencao / d.eleitores_tot
    d["rb"] = (d.brancos + d.nulos) / d.comparecimento.where(d.comparecimento > 0)
    b22 = hist["2022"]
    d["ra22"] = [1 - b22[u][1] / b22[u][0] for u in d.uf]
    d["rb22"] = [(b22[u][2] + b22[u][3]) / b22[u][1] for u in d.uf]
    d["ap"] = d.uf.map(aptos)
    sw_a = ((d.ra - d.ra22) * d.ap).sum() / d.ap.sum()
    sw_b = ((d.rb - d.rb22) * d.ap).sum() / d.ap.sum()
    abst_bruta = d.abstencao.sum() / d.eleitores_tot.sum()
    bn_bruta = (d.brancos + d.nulos).sum() / d.comparecimento.sum()
    tot_ap = tot_abs = tot_comp = tot_bn = 0.0
    obs = d.set_index("uf")
    for uf, ap in aptos.items():
        if uf in obs.index:
            ra, rb = obs.at[uf, "ra"], obs.at[uf, "rb"]
        else:
            ra = 1 - b22[uf][1] / b22[uf][0] + sw_a
            rb = (b22[uf][2] + b22[uf][3]) / b22[uf][1] + sw_b
        comp = ap * (1 - ra)
        tot_ap += ap; tot_abs += ap * ra; tot_comp += comp; tot_bn += comp * rb
    return {"abst_bruta": 100 * abst_bruta, "abst_proj": 100 * tot_abs / tot_ap,
            "bn_bruta": 100 * bn_bruta, "bn_proj": 100 * tot_bn / tot_comp}


def ultimas(con, eleicao: str, ate: str | None = None) -> pd.DataFrame:
    q = """SELECT l.* FROM leituras l JOIN (
             SELECT uf, MAX(coleta) mc FROM leituras WHERE eleicao=? {f} GROUP BY uf
           ) u ON l.uf=u.uf AND l.coleta=u.mc WHERE l.eleicao=?"""
    q = q.format(f="AND coleta<=?" if ate else "")
    args = (eleicao, ate, eleicao) if ate else (eleicao, eleicao)
    df = pd.read_sql_query(q, con, params=args)
    # se dois coletores gravaram no mesmo instante, fica uma linha por UF (evita índice duplicado)
    return df.drop_duplicates(subset="uf", keep="last").reset_index(drop=True)


def projetar(df: pd.DataFrame, hist: dict, pesos: dict) -> tuple[float | None, float | None]:
    """Devolve (parcial_bruta_lula, projecao_lula) em % dos votos Lula+Flávio."""
    d = df[df.uf != "BR"].copy()
    if d.empty or (d.lula + d.flavio).sum() == 0:
        return None, None
    bruto = 100 * d.lula.sum() / (d.lula + d.flavio).sum()
    d["lp"] = 100 * d.lula / (d.lula + d.flavio).where(lambda s: s > 0)
    d["b22"] = [100 * hist["2022"][u][4] / (hist["2022"][u][4] + hist["2022"][u][5]) for u in d.uf]
    d["w"] = d.uf.map(pesos)
    rep = d[(d.pst >= MIN_PST) & d.lp.notna()]
    if rep.empty:
        return bruto, None
    swing = ((rep.lp - rep.b22) * rep.w).sum() / rep.w.sum()
    # peso da UF = votos já apurados + fração restante do total esperado (1º turno calibrado por k);
    # com 100% apurado o peso é o total real, e a projeção coincide com o resultado.
    extrap = (rep.lula + rep.flavio) / (rep.pst / 100)
    k = extrap.sum() / rep.w.sum()
    total, soma = 0.0, 0.0
    for uf, w in pesos.items():
        linha = d[d.uf == uf]
        if not linha.empty and linha.pst.iloc[0] >= MIN_PST and pd.notna(linha.lp.iloc[0]):
            s = linha.lp.iloc[0]
            a = min(1.0, linha.pst.iloc[0] / 100)
            peso = (linha.lula.iloc[0] + linha.flavio.iloc[0]) + (1 - a) * w * k  # já apurado + restante esperado
        else:
            b = hist["2022"][uf]
            s = 100 * b[4] / (b[4] + b[5]) + swing
            peso = w * k
        total += s * peso
        soma += peso
    return bruto, total / soma


_SERIE_CACHE: dict[tuple, dict] = {}  # pontos já calculados não mudam: (eleicao, coleta) -> ponto


def serie(con, eleicao: str, hist: dict, pesos: dict) -> list[dict]:
    """Uma leitura do banco inteira + estado acumulado por coleta (rápido mesmo com milhares de linhas)."""
    todas = pd.read_sql_query("SELECT * FROM leituras WHERE eleicao=? ORDER BY coleta", con, params=(eleicao,))
    if todas.empty:
        return []
    coletas = list(dict.fromkeys(todas.coleta))[-240:]  # últimas 240 coletas com dado novo
    aptos = aptos_2026()
    out, estado = [], {}
    r = lambda x: None if x is None else round(float(x), 3)
    for c, grupo in todas.groupby("coleta", sort=True):
        for linha in grupo.itertuples(index=False):
            estado[linha.uf] = linha
        if c not in coletas:
            continue
        ch = (eleicao, c)
        if ch not in _SERIE_CACHE:
            df = pd.DataFrame(list(estado.values()))
            br = df[df.uf == "BR"]
            b, p = projetar(df, hist, pesos)
            cp = projetar_comparecimento(df, hist, aptos)
            _SERIE_CACHE[ch] = None if b is None else {
                "t": c[11:16], "pst": float(br.pst.iloc[0]) if not br.empty else None, "bruto": r(b), "proj": r(p),
                "abst_bruta": r(cp["abst_bruta"]), "abst_proj": r(cp["abst_proj"])}
        if _SERIE_CACHE[ch]:
            out.append(_SERIE_CACHE[ch])
    return out


def montar_payload(con, eleicao: str, status: dict) -> dict:
    hist, pesos = carregar_bases()
    df = ultimas(con, eleicao)
    ufs = {r.uf: {"pst": r.pst, "lula": int(r.lula), "flavio": int(r.flavio), "eleitores": int(r.eleitores_tot),
                  "comp": int(r.comparecimento), "abst": int(r.abstencao), "validos": int(r.validos), "brancos": int(r.brancos), "nulos": int(r.nulos),
                  "tse_hora": r.tse_hora}
           for r in df.itertuples()}
    bruto, proj = projetar(df, hist, pesos)
    aptos = aptos_2026()
    return {"eleicao": eleicao, "status": status, "ufs": ufs, "bruto": bruto, "proj": proj,
            "comp": projetar_comparecimento(df, hist, aptos), "aptos26": aptos, "outros_t1": outros_t1(),
            "serie": serie(con, eleicao, hist, pesos), "hist": hist, "pesos": pesos}
