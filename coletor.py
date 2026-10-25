"""
Coletor de resultados do TSE (presidente, por UF) -> SQLite.

Formato 2026: arquivos assinados (JWS, EdDSA) em
  {BASE}/oficial/ele2026/{eleicao}/dados/{uf}/{uf}-c0001-e{eleicao:06d}-u.jws
O payload (2ª parte do JWS) é JSON em base64url.

Uso direto:  python coletor.py            (coleta contínua, 60 s)
             python coletor.py --uma-vez  (uma rodada e sai)
Variáveis de ambiente opcionais:
  MONITOR_BASE      default https://resultados.tse.jus.br
  MONITOR_ELEICAO   força o código (ex.: 6257 = 1º turno, para teste)
  MONITOR_INTERVALO segundos entre rodadas (mín. 60 em produção)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")  # o servidor na nuvem roda em UTC


# --- Plano B de DNS --------------------------------------------------------------------------
# Se o DNS do Windows falhar ao resolver *.tse.jus.br (erro "getaddrinfo failed"), o endereço é
# obtido por DNS-over-HTTPS (Cloudflare 1.1.1.1 / Google 8.8.8.8, acessados por IP) e guardado em
# memória. A conexão continua usando o nome do TSE (certificado HTTPS validado normalmente).
import socket as _socket

_getaddrinfo_original = _socket.getaddrinfo
_DNS_CACHE: dict = {}


def _resolver_doh(host: str) -> list:
    for url in ("https://1.1.1.1/dns-query?name={}&type=A", "https://8.8.8.8/resolve?name={}&type=A"):
        try:
            r = requests.get(url.format(host), headers={"accept": "application/dns-json"}, timeout=5)
            ips = [a["data"] for a in r.json().get("Answer", []) if a.get("type") == 1]
            if ips:
                return ips
        except Exception:  # noqa: BLE001
            continue
    return []


def _getaddrinfo_com_plano_b(host, *args, **kwargs):
    try:
        return _getaddrinfo_original(host, *args, **kwargs)
    except _socket.gaierror:
        if isinstance(host, str) and host.endswith("tse.jus.br"):
            ips = _DNS_CACHE.get(host) or _resolver_doh(host)
            if ips:
                _DNS_CACHE[host] = ips
                return _getaddrinfo_original(ips[0], *args, **kwargs)
        raise


_socket.getaddrinfo = _getaddrinfo_com_plano_b

BASE = os.environ.get("MONITOR_BASE", "https://resultados.tse.jus.br").rstrip("/")
AMBIENTE = "oficial"
CICLO = "ele2026"
INTERVALO = int(os.environ.get("MONITOR_INTERVALO", "60"))
PASTA = Path(__file__).resolve().parent
DB = PASTA / "apuracao.db"

UFS = ["ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms", "mg", "pa",
       "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc", "sp", "se", "to", "zz", "br"]
NUM_LULA, NUM_FLAVIO = "13", "22"

log = logging.getLogger("coletor")


# ---------------------------------------------------------------- parsing
def decode_jws(texto: str) -> dict:
    """Devolve o payload JSON de um JWS compacto (header.payload.assinatura)."""
    partes = texto.strip().split(".")
    if len(partes) != 3:
        # tolera JSON puro (formato de anos anteriores)
        return json.loads(texto)
    p = partes[1] + "=" * (-len(partes[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(p).decode("utf-8"))


def num(v) -> float:
    """'1.234,56' / '1234,56' / '1234' -> float. Vazio -> 0."""
    if v in (None, ""):
        return 0.0
    s = str(v)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _candidatos(no):
    """Percorre o JSON e devolve todos os dicts de candidato (têm 'n' e 'vap')."""
    if isinstance(no, dict):
        if "vap" in no and "n" in no:
            yield no
        for v in no.values():
            yield from _candidatos(v)
    elif isinstance(no, list):
        for v in no:
            yield from _candidatos(v)


def extrair(payload: dict) -> dict:
    cargos = [c for c in payload.get("carg", []) if str(c.get("cd")) == "1"] or payload.get("carg", [])
    votos = {str(c["n"]): int(num(c["vap"])) for c in _candidatos(cargos)}
    s, e, v = payload.get("s", {}), payload.get("e", {}), payload.get("v", {})
    return {
        "pst": num(s.get("pst")),            # % seções totalizadas
        "secoes_tot": int(num(s.get("st"))),
        "secoes": int(num(s.get("ts"))),
        "eleitores_tot": int(num(e.get("est"))),   # aptos nas seções totalizadas
        "comparecimento": int(num(e.get("c"))),
        "abstencao": int(num(e.get("a"))),
        "validos": int(num(v.get("vv"))),
        "brancos": int(num(v.get("vb"))),
        "nulos": int(num(v.get("tvn"))),
        "lula": votos.get(NUM_LULA, 0),
        "flavio": votos.get(NUM_FLAVIO, 0),
        "outros": sum(x for k, x in votos.items() if k not in (NUM_LULA, NUM_FLAVIO)),
        "tse_hora": f'{payload.get("dg", "")} {payload.get("hg", "")}'.strip(),
    }


# ---------------------------------------------------------------- banco
def conectar(db: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(db, check_same_thread=False, timeout=10)
    try:
        con.execute("PRAGMA journal_mode=WAL")  # leitura não bloqueia gravação
    except sqlite3.Error:
        pass
    con.execute("""CREATE TABLE IF NOT EXISTS leituras(
        coleta TEXT, eleicao TEXT, uf TEXT, pst REAL, secoes_tot INT, secoes INT,
        eleitores_tot INT, comparecimento INT, abstencao INT, validos INT, brancos INT,
        nulos INT, lula INT, flavio INT, outros INT, tse_hora TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_l ON leituras(eleicao, uf, coleta)")
    con.execute("CREATE TABLE IF NOT EXISTS status(chave TEXT PRIMARY KEY, valor TEXT)")
    con.commit()
    return con


CHAVE = "pst||'|'||lula||'|'||flavio||'|'||comparecimento||'|'||abstencao||'|'||brancos||'|'||nulos||'|'||eleitores_tot"


def compactar(con) -> int:
    """Apaga leituras repetidas (mesmos números da leitura anterior da mesma UF). Devolve nº apagado."""
    try:
        cur = con.execute(f"""DELETE FROM leituras WHERE rowid IN (
            SELECT rid FROM (SELECT rowid AS rid, {CHAVE} AS k,
                LAG({CHAVE}) OVER (PARTITION BY eleicao, uf ORDER BY coleta) AS kp FROM leituras)
            WHERE k = kp)""")
        con.commit()
        return cur.rowcount
    except sqlite3.OperationalError:   # SQLite antigo sem funções de janela: segue sem compactar
        return 0


def set_status(con, **kw):
    for k, v in kw.items():
        con.execute("INSERT OR REPLACE INTO status VALUES(?,?)", (k, str(v)))
    con.commit()


# ---------------------------------------------------------------- coleta
class Coletor:
    def __init__(self, eleicao: str | None = None, db: Path = DB, base: str | None = None,
                 turno: int | None = None):
        self.turno = turno
        self.base = (base or BASE).rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) monitor-eleicoes/1.0",
            "Accept": "application/json, */*",
        })
        self.etags: dict[str, str] = {}
        self.eleicao = eleicao or os.environ.get("MONITOR_ELEICAO")
        self.nome_eleicao = ""
        self.db = db
        self.con = conectar(db)
        self.thread = None
        import uuid
        self.id = uuid.uuid4().hex
        try:   # este coletor passa a ser o único dono do banco; os antigos se desligam sozinhos
            set_status(self.con, dono=self.id)
        except sqlite3.Error:
            pass
        self._parar = threading.Event()
        self._agora = threading.Event()
        self.lm: dict[str, str] = {}
        removidas = compactar(self.con)
        if removidas:
            log.info("Removidas %d leituras repetidas", removidas)
        # último valor gravado por UF (evita regravar o mesmo dado após reiniciar o app)
        self.ultimo: dict[tuple, tuple] = {}
        for row in self.con.execute(f"""SELECT eleicao, uf, {CHAVE} FROM leituras l WHERE coleta =
                (SELECT MAX(coleta) FROM leituras m WHERE m.eleicao=l.eleicao AND m.uf=l.uf)"""):
            self.ultimo[(row[0], row[1])] = row[2]

    def detectar_eleicao(self) -> str:
        """Lê a lista oficial e escolhe a eleição federal 2026 mais recente (2º turno se existir)."""
        r = self.s.get(f"{self.base}/{AMBIENTE}/comum/config/ele-c.json", timeout=20)
        r.raise_for_status()
        cfg = decode_jws(r.text)
        cands = []
        for pl in cfg.get("pl", []):
            for e in pl.get("e", []):
                nm = e.get("nm", "")
                if "Federal" in nm and "2026" in nm and "Suplementar" not in nm:
                    cands.append((int(e.get("t", 1)), e["cd"], nm))
        if self.turno:
            cands = [c for c in cands if c[0] == self.turno]
        if not cands:
            raise RuntimeError(f"{self.turno or ''}º turno federal 2026 ainda não está na lista do TSE; "
                               "nova tentativa a cada 60 s")
        t, cd, nm = max(cands)
        self.nome_eleicao = nm
        return str(cd)

    def url(self, uf: str) -> str:
        e = int(self.eleicao)
        return f"{self.base}/{AMBIENTE}/{CICLO}/{e}/dados/{uf}/{uf}-c0001-e{e:06d}-u.jws"

    def rodada(self) -> int:
        """Baixa as 29 abrangências; grava só o que mudou. Devolve nº de UFs atualizadas."""
        if not self.eleicao:
            self.eleicao = self.detectar_eleicao()
            log.info("Eleição detectada: %s (%s)", self.eleicao, self.nome_eleicao)
        agora = datetime.now(TZ).replace(tzinfo=None).isoformat(timespec="seconds")  # horário de Brasília
        novos, erros = [], []
        for uf in UFS:
            h = {}
            if uf in self.etags:
                h["If-None-Match"] = self.etags[uf]
            if uf in self.lm:
                h["If-Modified-Since"] = self.lm[uf]
            try:
                r = self.s.get(self.url(uf), headers=h, timeout=20)
                if r.status_code == 304:
                    continue
                if r.status_code == 404:          # arquivo ainda não publicado (antes das 17h)
                    continue
                r.raise_for_status()
                d = extrair(decode_jws(r.text))
                if r.headers.get("ETag"):
                    self.etags[uf] = r.headers["ETag"]
                if r.headers.get("Last-Modified"):
                    self.lm[uf] = r.headers["Last-Modified"]
                # o servidor do TSE nem sempre responde 304: compara o conteúdo e só grava se mudou
                k = "|".join(str(x) for x in (d["pst"], d["lula"], d["flavio"], d["comparecimento"],
                                               d["abstencao"], d["brancos"], d["nulos"], d["eleitores_tot"]))
                chave_uf = (self.eleicao, uf.upper())
                if self.ultimo.get(chave_uf) == k:
                    continue
                self.ultimo[chave_uf] = k
                novos.append((agora, self.eleicao, uf.upper(), d["pst"], d["secoes_tot"], d["secoes"],
                              d["eleitores_tot"], d["comparecimento"], d["abstencao"], d["validos"],
                              d["brancos"], d["nulos"], d["lula"], d["flavio"], d["outros"], d["tse_hora"]))
            except Exception as ex:  # noqa: BLE001 — uma UF com erro não derruba a rodada
                erros.append(f"{uf}: {ex}")
            time.sleep(0.05)  # ~20 req/s, bem abaixo do limite de 100/s por IP
        if novos:
            self.con.executemany("INSERT INTO leituras VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", novos)
        set_status(self.con, ultima_rodada=agora, eleicao=self.eleicao, nome_eleicao=self.nome_eleicao,
                   ufs_atualizadas=len(novos), erros=" | ".join(erros[:5]))
        if erros:
            log.warning("%d erros: %s", len(erros), erros[:3])
        log.info("Rodada %s: %d UFs atualizadas", agora, len(novos))
        return len(novos)

    def loop(self, intervalo: int = INTERVALO):
        while not self._parar.is_set():
            t0 = time.time()
            try:
                dono = self.con.execute("SELECT valor FROM status WHERE chave='dono'").fetchone()
                if dono and dono[0] != self.id:
                    log.info("Outro coletor assumiu este banco; encerrando este.")
                    break
            except sqlite3.Error:
                pass
            try:
                self.rodada()
            except Exception as ex:  # noqa: BLE001
                log.error("Falha na rodada: %s", ex)
                if isinstance(ex, sqlite3.Error):   # banco travado/somente leitura: reabre a conexão
                    try:
                        self.con = conectar(self.db)
                    except Exception as ex2:  # noqa: BLE001
                        log.error("Não reabriu o banco: %s", ex2)
                try:
                    set_status(self.con, erros=str(ex))
                except Exception:  # noqa: BLE001 — nunca deixar a thread morrer
                    pass
            self._agora.wait(max(1, intervalo - (time.time() - t0)))
            self._agora.clear()

    def iniciar_em_thread(self, intervalo: int = INTERVALO) -> threading.Thread:
        th = threading.Thread(target=self.loop, args=(intervalo,), daemon=True, name="coletor-tse")
        th.start()
        self.thread = th
        return th

    def buscar_agora(self):
        """Antecipa a próxima rodada (botão "Buscar no TSE agora")."""
        self._agora.set()

    def vivo(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def parar(self):
        self._parar.set()
        self._agora.set()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--uma-vez", action="store_true")
    ap.add_argument("--eleicao", help="código da eleição (6257 = 1º turno 2026)")
    a = ap.parse_args()
    c = Coletor(a.eleicao)
    c.rodada() if a.uma_vez else c.loop()
