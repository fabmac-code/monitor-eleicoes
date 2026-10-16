"""
Simulador local do servidor de resultados do TSE (formato 2026, .jws).

Reproduz uma apuração de 2º turno a partir dos números do 1º turno de 2026
(votos dos demais candidatos redistribuídos 45% Lula / 55% Flávio), com a ordem
típica de chegada das urnas (Sul/Sudeste/Centro-Oeste antes, Norte/Nordeste e
exterior depois) e um viés intra-UF que se desfaz ao longo da contagem.

  python simulador_tse.py --minutos 15      (apuração completa em 15 min)

Serve em http://127.0.0.1:8765 . Não são resultados reais.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
COD = 6258
REG = {"AC": "N", "AM": "N", "AP": "N", "PA": "N", "RO": "N", "RR": "N", "TO": "N", "AL": "NE", "BA": "NE",
       "CE": "NE", "MA": "NE", "PB": "NE", "PE": "NE", "PI": "NE", "RN": "NE", "SE": "NE", "DF": "CO",
       "GO": "CO", "MS": "CO", "MT": "CO", "ES": "SE", "MG": "SE", "RJ": "SE", "SP": "SE", "PR": "S",
       "RS": "S", "SC": "S", "ZZ": "ZZ"}
VEL = {"S": .65, "SE": .75, "CO": .7, "N": 1.25, "NE": 1.45, "ZZ": 2.2}

rnd = random.Random(42)
BASE = {}
for r in csv.DictReader(open(PASTA / "primeiro_turno_2026_uf.csv", encoding="utf-8")):
    o = int(r["outros"])
    ap, c1 = int(r["aptos"]), int(r["comparecimento"])
    l1, f1 = int(r["lula"]) + o * .45, int(r["flavio"]) + o * .55
    # 2º turno: abstenção da UF varia -2..+3 p.p. vs 1º turno; brancos+nulos sobem 10-40%
    ra = min(.9, 1 - c1 / ap + rnd.uniform(-.02, .03))
    rb = (int(r["brancos"]) + int(r["nulos"])) / c1 * rnd.uniform(1.1, 1.4)
    BASE[r["uf"]] = {"ap": ap, "ra": ra, "rb": rb, "share": l1 / (l1 + f1),
                     "vies": rnd.uniform(-4, 4), "vies_a": rnd.uniform(-.03, .03), "sec": max(50, ap // 320)}
T0 = time.time()
DUR = 900.0


def b64(o) -> str:
    return base64.urlsafe_b64encode(json.dumps(o, ensure_ascii=False).encode()).decode().rstrip("=")


def fmt(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def estado(uf: str, t: float) -> dict:
    b = BASE[uf]
    p = 1.0 if t >= 1 else min(1.0, t ** VEL[REG[uf]]) if t > 0 else 0.0
    share = b["share"] + b["vies"] / 100 * (1 - p)        # seções iniciais enviesadas, convergem
    ra = min(.95, max(.02, b["ra"] + b["vies_a"] * (1 - p)))
    ap = b["ap"] * p
    c = ap * (1 - ra)
    bn = c * b["rb"]
    tot = c - bn
    return {"p": p, "l": round(tot * share), "f": round(tot * (1 - share)), "ap": ap, "c": c,
            "vb": bn * .4, "vn": bn * .6, "sec": b["sec"]}


def payload(uf: str) -> dict:
    t = min(1.0, max(0.0, (time.time() - T0) / DUR))
    t = int(t * 60) / 60  # avança em degraus (simula lotes de totalização)
    ufs = list(BASE) if uf == "BR" else [uf]
    agg = {"l": 0, "f": 0, "ap": 0, "c": 0, "vb": 0, "vn": 0, "sec": 0, "st": 0}
    for u in ufs:
        e = estado(u, t)
        for k in ("l", "f", "ap", "c", "vb", "vn"):
            agg[k] += e[k]
        agg["sec"] += e["sec"]
        agg["st"] += e["sec"] * e["p"]
    pst = 100 * agg["st"] / agg["sec"]
    vv = agg["l"] + agg["f"]
    hora = time.strftime("%H:%M:%S")
    cand = lambda n, nm, v: {"n": n, "nmu": nm, "vap": str(int(v)), "pvap": fmt(100 * v / vv if vv else 0), "st": "", "e": "n"}
    return {"ele": str(COD), "t": "2", "cdabr": uf.lower(), "dg": time.strftime("%d/%m/%Y"), "hg": hora,
            "s": {"ts": str(agg["sec"]), "st": str(round(agg["st"])), "pst": fmt(pst)},
            "e": {"te": str(sum(BASE[u]["ap"] for u in ufs)), "est": str(round(agg["ap"])), "c": str(round(agg["c"])),
                  "a": str(round(agg["ap"] - agg["c"]))},
            "v": {"vv": str(vv), "vb": str(round(agg["vb"])), "tvn": str(round(agg["vn"]))},
            "carg": [{"cd": "1", "agr": [{"par": [{"n": "13", "cand": [cand("13", "LULA", agg["l"])]}]},
                                         {"par": [{"n": "22", "cand": [cand("22", "FLAVIO BOLSONARO", agg["f"])]}]}]}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("/comum/config/ele-c.json"):
            corpo = json.dumps({"pl": [{"cd": "3220", "dt": "04/10/2026", "e": [
                {"cd": "6257", "nm": "Eleição Ordinária Federal - 2026 1º Turno", "t": "1"}]},
                {"cd": "3221", "dt": "25/10/2026", "e": [
                    {"cd": str(COD), "nm": "Eleição Ordinária Federal - 2026 2º Turno (SIMULADO)", "t": "2"}]}]}).encode()
        elif f"/ele2026/{COD}/dados/" in path and path.endswith("-u.jws"):
            uf = path.rsplit("/", 1)[1][:2].upper()
            if uf not in BASE and uf != "BR":
                return self.send_error(404)
            corpo = (b64({"alg": "none", "sim": True}) + "." + b64(payload(uf)) + ".SIMULADO").encode()
        else:
            return self.send_error(404)
        etag = '"' + hashlib.md5(corpo).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.end_headers(); return
        self.send_response(200)
        self.send_header("ETag", etag)
        self.send_header("Content-Type", "application/jose")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=15)
    ap.add_argument("--porta", type=int, default=8765)
    a = ap.parse_args()
    DUR = a.minutos * 60
    print(f"Simulador TSE em http://127.0.0.1:{a.porta}  (apuração completa em {a.minutos:g} min). Ctrl+C para sair.")
    ThreadingHTTPServer(("127.0.0.1", a.porta), H).serve_forever()
