#!/usr/bin/env python3
"""Capa do Mundo: lê os feeds RSS/Atom dos veículos de outlets.csv, agrupa manchetes
repetidas (mesma notícia em vários veículos) e gera a página docs/index.html.

Uso:
    python capa.py                 # execução normal (rede)
    python capa.py --demo          # prévia com dados fictícios de tests/amostra.json (sem rede)
    python capa.py --backend tfidf # força o agrupamento leve (sem baixar modelo)
    python capa.py --limiar 0.70   # ajusta o rigor do agrupamento (0 a 1; maior = mais rígido)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import html
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

RAIZ = Path(__file__).resolve().parent
FUSO = ZoneInfo("America/Sao_Paulo")
UA = "CapaDoMundoBot/1.0 (agregador pessoal de manchetes; le feeds RSS publicos)"

MAX_POR_VEICULO = 4       # manchetes por veículo
IDADE_MAX_H = 36          # ignora itens mais velhos que isso
TIMEOUT = 12              # segundos, feed principal
TIMEOUT_SONDA = 6         # segundos, tentativas de descobrir feed
TRABALHADORES = 32
REVERIFICAR_DIAS = 7      # veículo sem feed só é re-sondado após esse prazo
MAX_ITENS_TOTAL = 3000    # teto de segurança para a matriz de similaridade

LIMIAR_PADRAO = {"embeddings": 0.72, "tfidf": 0.36}
MODELO_EMBEDDINGS = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

CAMINHOS_FEED = ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/rss/all.xml"]
PARAMS_RASTREIO = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "cid", "smid", "at_medium"}

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]
ORDEM_REGIOES = ["Internacional", "América do Norte", "América Latina", "Europa",
                 "Oriente Médio e Norte da África", "África Subsaariana", "Ásia", "Oceania"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- entrada

def carregar_veiculos() -> list[dict]:
    with open(RAIZ / "outlets.csv", newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in linha.items()} for linha in csv.DictReader(f)]


# --------------------------------------------------------------------------- coleta

def limpar_texto(s: str | None) -> str:
    if not s:
        return ""
    texto = BeautifulSoup(s, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def url_limpa(u: str) -> str | None:
    p = urlparse(u.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in PARAMS_RASTREIO]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))


def _get(url: str, timeout: int) -> requests.Response:
    return requests.get(
        url, timeout=timeout, allow_redirects=True,
        headers={"User-Agent": UA,
                 "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5"},
    )


def baixar_feed(url: str, timeout: int = TIMEOUT):
    """Baixa e valida um feed. Devolve o objeto do feedparser ou None."""
    try:
        r = _get(url, timeout)
        if not r.ok or len(r.content) > 8_000_000:
            return None
        d = feedparser.parse(r.content)
        return d if d.entries else None
    except requests.RequestException:
        return None


def descobrir_feed(site: str) -> str | None:
    """Procura o feed do veículo: <link rel=alternate> na home e caminhos comuns."""
    candidatos: list[str] = []
    try:
        r = _get(site, TIMEOUT_SONDA)
        if r.ok:
            if feedparser.parse(r.content).entries:      # o próprio endereço já é um feed
                return r.url
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup.find_all("link", rel=lambda v: v and "alternate" in v):
                tipo = (tag.get("type") or "").lower()
                href = tag.get("href")
                if tipo in ("application/rss+xml", "application/atom+xml") and href:
                    href = urljoin(r.url, href)
                    if "comment" not in href.lower() and href not in candidatos:
                        candidatos.append(href)
    except requests.RequestException:
        pass
    base = site.rstrip("/")
    raiz = "{0.scheme}://{0.netloc}".format(urlparse(site))
    for prefixo in dict.fromkeys([base, raiz]):
        for caminho in CAMINHOS_FEED:
            u = prefixo + caminho
            if u not in candidatos:
                candidatos.append(u)
    for u in candidatos[:10]:
        if baixar_feed(u, TIMEOUT_SONDA):
            return u
    return None


def data_item(e):
    for campo in ("published_parsed", "updated_parsed"):
        if e.get(campo):
            try:
                return datetime(*e[campo][:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
    # Alguns feeds brasileiros usam dias e meses em português, fora do RFC 822.
    meses = {"jan": "Jan", "fev": "Feb", "mar": "Mar", "abr": "Apr", "mai": "May",
             "jun": "Jun", "jul": "Jul", "ago": "Aug", "set": "Sep", "out": "Oct",
             "nov": "Nov", "dez": "Dec"}
    for campo in ("published", "updated"):
        bruto = str(e.get(campo) or "")
        bruto = re.sub(r"^\w+,\s*", "", bruto)
        for pt, en in meses.items():
            bruto = re.sub(r"\b" + pt + r"\b", en, bruto, flags=re.I)
        try:
            data = parsedate_to_datetime(bruto)
            if data.tzinfo is not None:
                return data.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def extrair_itens(veiculo: dict, feed, agora: datetime) -> list[dict]:
    itens, vistos = [], set()
    limite = agora - timedelta(hours=IDADE_MAX_H)
    for e in feed.entries:
        titulo = limpar_texto(e.get("title"))
        link = url_limpa(e.get("link") or "")
        if len(titulo) < 12 or not link or link in vistos or titulo.lower() in vistos:
            continue
        quando = data_item(e)
        if quando is None or quando < limite:
            continue
        if quando and quando > agora + timedelta(hours=6):
            continue
        vistos.update([link, titulo.lower()])
        itens.append({
            "veiculo": veiculo["nome"], "site": veiculo["site"], "pais": veiculo["pais"],
            "regiao": veiculo["regiao"], "titulo": titulo, "url": link,
            "publicado": quando.isoformat() if quando else None,
        })
        if len(itens) >= MAX_POR_VEICULO:
            break
    return itens


def processar_veiculo(veiculo: dict, entrada_cache: dict | None, agora: datetime):
    feed_url = veiculo.get("feed") or (entrada_cache or {}).get("feed")
    d = baixar_feed(feed_url) if feed_url else None
    if not d:
        if (not veiculo.get("feed") and entrada_cache and not entrada_cache.get("feed")
                and entrada_cache.get("checado_em")):
            idade = agora - datetime.fromisoformat(entrada_cache["checado_em"])
            if idade < timedelta(days=REVERIFICAR_DIAS):
                return None, [], "sem feed (verificado recentemente)"
        feed_url = descobrir_feed(veiculo["site"])
        d = baixar_feed(feed_url) if feed_url else None
    if not d:
        return None, [], "feed não encontrado ou fora do ar"
    itens = extrair_itens(veiculo, d, agora)
    if not itens:
        return feed_url, [], "feed sem itens recentes"
    return feed_url, itens, None


def coletar(veiculos: list[dict], agora: datetime):
    caminho_cache = RAIZ / "data" / "feeds_cache.json"
    cache = json.loads(caminho_cache.read_text("utf-8")) if caminho_cache.exists() else {}
    itens, falhas, com_itens = [], [], 0
    with cf.ThreadPoolExecutor(TRABALHADORES) as ex:
        futuros = {ex.submit(processar_veiculo, v, cache.get(v["site"]), agora): v for v in veiculos}
        for fut in cf.as_completed(futuros):
            v = futuros[fut]
            try:
                feed_url, its, erro = fut.result()
            except Exception as exc:  # um veículo problemático não derruba a edição
                feed_url, its, erro = None, [], f"erro inesperado: {exc!r}"
            if not (erro and "recentemente" in erro):      # pulado por já ter sido sondado: não renova o prazo
                antigo = (cache.get(v["site"]) or {}).get("feed")
                cache[v["site"]] = {"feed": feed_url or antigo, "checado_em": agora.isoformat()}
            if its:
                itens.extend(its)
                com_itens += 1
            else:
                falhas.append({"veiculo": v["nome"], "motivo": erro})
    caminho_cache.parent.mkdir(exist_ok=True)
    caminho_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    itens.sort(key=lambda i: (i["veiculo"], i["url"]))
    return itens, {"consultados": len(veiculos), "com_manchetes": com_itens, "falhas": sorted(falhas, key=lambda x: x["veiculo"])}


def carregar_demo(veiculos: list[dict], agora: datetime):
    por_nome = {v["nome"]: v for v in veiculos}
    bruto = json.loads((RAIZ / "tests" / "amostra.json").read_text("utf-8"))
    itens = []
    for i in bruto:
        v = por_nome.get(i["veiculo"])
        if not v:
            continue
        itens.append({"veiculo": v["nome"], "site": v["site"], "pais": v["pais"], "regiao": v["regiao"],
                      "titulo": i["titulo"], "url": v["site"], "publicado": agora.isoformat()})
    nomes = {i["veiculo"] for i in itens}
    return itens, {"consultados": len(veiculos), "com_manchetes": len(nomes), "falhas": []}


# --------------------------------------------------------------------------- agrupamento

def matriz_similaridade(textos: list[str], backend: str) -> np.ndarray:
    if backend == "embeddings":
        from fastembed import TextEmbedding
        modelo = TextEmbedding(MODELO_EMBEDDINGS, cache_dir=str(RAIZ / ".model_cache"))
        v = np.array(list(modelo.embed(textos)), dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        return v @ v.T
    from sklearn.feature_extraction.text import TfidfVectorizer
    x = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True,
                        strip_accents="unicode").fit_transform(textos)
    return (x @ x.T).toarray().astype(np.float32)


def agrupar(textos: list[str], backend: str, limiar: float):
    """Devolve (grupos, matriz de similaridade, backend usado, limiar usado)."""
    n = len(textos)
    if n < 2:
        return [[i] for i in range(n)], np.ones((n, n), dtype=np.float32), backend, limiar
    try:
        sim = matriz_similaridade(textos, backend)
    except Exception as exc:
        if backend == "tfidf":
            raise
        log(f"embeddings indisponíveis ({exc!r}); usando tfidf (só agrupa bem manchetes no mesmo idioma)")
        backend, limiar = "tfidf", LIMIAR_PADRAO["tfidf"]
        sim = matriz_similaridade(textos, backend)
    dist = np.clip(1.0 - (sim + sim.T) / 2, 0.0, None).astype(np.float64)
    np.fill_diagonal(dist, 0.0)
    rotulos = fcluster(linkage(squareform(dist, checks=False), method="average"),
                       t=1.0 - limiar, criterion="distance")
    grupos: dict[int, list[int]] = defaultdict(list)
    for idx, r in enumerate(rotulos):
        grupos[r].append(idx)
    return list(grupos.values()), sim, backend, limiar


def montar_historias(itens: list[dict], grupos: list[list[int]], sim: np.ndarray):
    """Grupos com 2+ veículos viram 'histórias' (com todos os links); o resto volta como avulsas."""
    historias, consumidos = [], set()
    for g in grupos:
        por_veiculo: dict[str, list[int]] = defaultdict(list)
        for idx in g:
            por_veiculo[itens[idx]["veiculo"]].append(idx)
        if len(por_veiculo) < 2:
            continue
        medoide = g[int(np.argmax(sim[np.ix_(g, g)].mean(axis=1)))]
        fontes = []
        for veiculo, idxs in por_veiculo.items():
            it = itens[medoide if medoide in idxs else idxs[0]]
            fontes.append({"veiculo": veiculo, "url": it["url"], "titulo": it["titulo_exibicao"],
                           "original": it["titulo"], "regiao": it["regiao"], "site": it["site"]})
            consumidos.update(idxs)
        fontes.sort(key=lambda f: (f["veiculo"] != itens[medoide]["veiculo"], f["veiculo"].lower()))
        datas = [itens[i]["publicado"] for i in g if itens[i]["publicado"]]
        rep = itens[medoide]
        historias.append({
            "titulo": rep["titulo_exibicao"], "original": rep["titulo"], "url": rep["url"], "fontes": fontes,
            "n": len(fontes), "n_regioes": len({f["regiao"] for f in fontes}),
            "recente": max(datas) if datas else "",
        })
    historias.sort(key=lambda h: (h["n"] + 0.5 * h["n_regioes"], h["recente"]), reverse=True)
    for h in historias:
        h["peso"] = 5 if h["n"] >= 10 else 4 if h["n"] >= 5 else 3 if h["n"] >= 3 else 2
        h["busca"] = " ".join([h["titulo"], h["original"]] + [f["veiculo"] + " " + f["titulo"] for f in h["fontes"]]).lower()
    return historias, consumidos


def montar_regioes(itens: list[dict], consumidos: set[int]):
    por_regiao: dict[str, dict[str, dict]] = defaultdict(dict)
    for idx, it in enumerate(itens):
        if idx in consumidos:
            continue
        regiao = (it.get("regiao_br") or "Nacionais e especializados") if it["pais"] == "Brasil" else it["regiao"]
        v = por_regiao[regiao].setdefault(
            it["veiculo"], {"nome": it["veiculo"], "site": it["site"], "pais": it["pais"],
                            "uf": it.get("uf", ""), "itens": []})
        v["itens"].append({"titulo": it["titulo_exibicao"], "original": it["titulo"], "url": it["url"]})
    regioes = []
    ordem = ["Nacionais e especializados", "Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"] + ORDEM_REGIOES
    for nome in sorted(por_regiao, key=lambda r: ordem.index(r) if r in ordem else 99):
        veics = sorted(por_regiao[nome].values(), key=lambda v: v["nome"].lower())
        for v in veics:
            v["busca"] = " ".join([v["nome"], v["pais"], v["uf"], nome] + [i["titulo"] + " " + i["original"] for i in v["itens"]]).lower()
        regioes.append({"nome": nome, "veiculos": veics, "total": sum(len(v["itens"]) for v in veics)})
    return regioes


def montar_secoes(itens, veiculos, falhas, backend, limiar):
    """Separa pelo país cadastrado do veículo, nunca pelo idioma ou assunto."""
    secoes, todas_historias = [], []
    cadastro = {v["nome"]: v for v in veiculos}
    for slug, nome, brasileiro in [("brasil", "Brasil", True), ("mundo", "Resto do mundo", False)]:
        fontes = [v for v in veiculos if (v["pais"] == "Brasil") == brasileiro]
        subset = [dict(i, uf=cadastro.get(i["veiculo"], {}).get("uf", ""),
                       regiao_br=cadastro.get(i["veiculo"], {}).get("regiao_br", ""))
                  for i in itens if (i["pais"] == "Brasil") == brasileiro]
        grupos, sim, usado, corte = agrupar([i["titulo_exibicao"] for i in subset], backend, limiar)
        historias, consumidos = montar_historias(subset, grupos, sim)
        destaque = historias[0] if historias else None
        resto = historias[1:]
        if not destaque and subset:
            idx = max(range(len(subset)), key=lambda j: subset[j]["publicado"] or "")
            it = subset[idx]
            destaque = dict(titulo=it["titulo_exibicao"], original=it["titulo"], url=it["url"], n=1,
                             busca=(it["titulo_exibicao"] + " " + it["veiculo"]).lower(),
                             fontes=[dict(veiculo=it["veiculo"], url=it["url"], titulo=it["titulo_exibicao"],
                                          original=it["titulo"])])
            consumidos.add(idx)
        nomes = {v["nome"] for v in fontes}
        cobertura = []
        if brasileiro:
            for uf in sorted({v.get("uf") for v in fontes if v.get("uf")}):
                locais = [v for v in fontes if v.get("uf") == uf]
                noticias = [i for i in subset if i.get("uf") == uf]
                cobertura.append(dict(uf=uf, fontes=locais, manchetes=len(noticias)))
        secoes.append(dict(id=slug, nome=nome, destaque=destaque, laterais=resto[:8], demais=resto[8:],
                           regioes=montar_regioes(subset, consumidos), consultados=len(fontes),
                           ativos=len({i["veiculo"] for i in subset}), manchetes=len(subset),
                           falhas=[f for f in falhas if f["veiculo"] in nomes], backend=usado, limiar=corte,
                           cobertura=cobertura))
        todas_historias.extend(historias)
    return secoes, todas_historias


# --------------------------------------------------------------------------- página

def data_extenso(d: datetime) -> str:
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def renderizar(contexto: dict, destino: Path) -> None:
    env = Environment(loader=FileSystemLoader(RAIZ / "templates"), autoescape=select_autoescape(["html"]))
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(env.get_template("capa.html").render(**contexto), "utf-8")


def principal() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="usa tests/amostra.json (sem rede) e marca a página como prévia")
    ap.add_argument("--backend", choices=["embeddings", "tfidf"], help="padrão: tfidf, gratuito e sem download de modelo")
    ap.add_argument("--limiar", type=float, help="similaridade mínima para juntar manchetes (0 a 1)")
    ap.add_argument("--max-veiculos", type=int, help="limita a quantidade de veículos (para testes)")
    ap.add_argument("--saida", default=str(RAIZ / "docs"), help="pasta de saída (padrão: docs)")
    ap.add_argument("--min-veiculos", type=int, default=20, help="cobertura mínima para publicar uma edição real")
    args = ap.parse_args()
    if args.limiar is not None and not 0 <= args.limiar <= 1:
        ap.error("--limiar deve estar entre 0 e 1")
    if args.min_veiculos < 1 or (args.max_veiculos is not None and args.max_veiculos < 1):
        ap.error("limites de veículos devem ser positivos")

    args.backend = args.backend or "tfidf"
    agora = datetime.now(FUSO)
    veiculos = carregar_veiculos()
    if args.max_veiculos:
        veiculos = veiculos[: args.max_veiculos]

    itens, status = carregar_demo(veiculos, agora) if args.demo else coletar(veiculos, agora.astimezone(timezone.utc))
    log(f"{status['com_manchetes']}/{status['consultados']} veículos com manchetes; {len(itens)} itens")
    if not itens:
        log("nenhuma manchete coletada; a edição anterior foi mantida")
        return 1
    if len(itens) > MAX_ITENS_TOTAL:
        itens = itens[:MAX_ITENS_TOTAL]

    if not args.demo:
        diagnostico = RAIZ / "data" / "ultima_tentativa.json"
        diagnostico.parent.mkdir(exist_ok=True)
        diagnostico.write_text(json.dumps({"gerada_em": agora.isoformat(), **status}, ensure_ascii=False, indent=2), "utf-8")
        minimo = min(args.min_veiculos, len(veiculos))
        if status["com_manchetes"] < minimo:
            log(f"cobertura insuficiente: {status['com_manchetes']}/{minimo}; edição anterior mantida")
            return 1
    for it in itens:
        it["titulo_exibicao"] = it["titulo"]

    limiar = args.limiar if args.limiar is not None else LIMIAR_PADRAO[args.backend]
    secoes, historias = montar_secoes(itens, veiculos, status["falhas"], args.backend, limiar)

    contexto = {
        "demo": args.demo,
        "edicao": {"data": data_extenso(agora), "data_curta": agora.strftime("%d/%m/%Y"),
                   "gerada_em": agora.strftime("%d/%m/%Y às %H:%M"), "iso": agora.date().isoformat()},
        "numeros": {"veiculos": status["com_manchetes"], "consultados": status["consultados"],
                    "manchetes": len(itens), "historias": len(historias)},
        "secoes": secoes, "traduzida": False,
    }
    saida = Path(args.saida)
    contexto["arquivo_href"] = "edicoes/index.html"
    renderizar(contexto, saida / "index.html")
    contexto["arquivo_href"] = "index.html"
    if not args.demo:
        renderizar(contexto, saida / "edicoes" / f"{contexto['edicao']['iso']}.html")
        edicoes = sorted((saida / "edicoes").glob("????-??-??.html"), reverse=True)
        for antiga in edicoes[90:]:
            antiga.unlink()
        links = "\n".join(f'<li><a href="{e.name}">{e.stem}</a></li>' for e in edicoes[:90])
        (saida / "edicoes" / "index.html").write_text(
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Edições — Capa do Mundo</title><body><h1>Edições anteriores</h1>'
            '<p><a href="../index.html">Voltar à capa atual</a></p><ul>' + links + '</ul></body></html>', "utf-8")
        resumo = RAIZ / "data" / "ultima_execucao.json"
        resumo.parent.mkdir(exist_ok=True)
        resumo.write_text(json.dumps({
            "gerada_em": agora.isoformat(), "numeros": contexto["numeros"], "backend": args.backend, "limiar": limiar,
            "falhas": status["falhas"],
            "secoes": [{k: s[k] for k in ("id", "nome", "consultados", "ativos", "manchetes", "backend", "limiar")} for s in secoes],
            "historias": [{"titulo": h["titulo"], "veiculos": [f["veiculo"] for f in h["fontes"]]} for h in historias],
        }, ensure_ascii=False, indent=1), "utf-8")
    log(f"ok: {len(historias)} histórias em vários veículos; página em {saida / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
