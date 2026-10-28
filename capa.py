#!/usr/bin/env python3
"""Capa do Mundo: coleta RSS públicos, seleciona cinco editorias e publica HTML estático."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import html
import json
import re
import sys
import shutil
import tempfile
import threading
from types import SimpleNamespace
from editorial import POR_ID, classificar, montar_editorias, tipo_publicacao
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

RAIZ = Path(__file__).resolve().parent
FUSO = ZoneInfo("America/Sao_Paulo")
UA = "CapaDoMundoBot/1.0 (agregador pessoal de manchetes; le feeds RSS publicos)"

TIMEOUT = 12
TIMEOUT_SONDA = 6
TRABALHADORES = 24
REVERIFICAR_DIAS = 7
CAMINHOS_FEED = ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/rss/all.xml"]
PARAMS_RASTREIO = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "cid", "smid", "at_medium"}
_LOCAL = threading.local()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- entrada

def carregar_veiculos():
    with (RAIZ / "outlets.csv").open(newline="", encoding="utf-8") as f:
        fontes = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    extras = json.loads((RAIZ / "fontes_tematicas.json").read_text("utf-8"))
    por_site = {v["site"].rstrip("/"): v for v in fontes}
    for v in extras:
        chave = v["site"].rstrip("/")
        if chave in por_site:
            por_site[chave].update({k: v[k] for k in ("tema", "tipo", "feed") if v.get(k)})
        else:
            fontes.append(v)
            por_site[chave] = v
    return fontes


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), "utf-8")
    temporario.replace(caminho)


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


def _get(url, timeout):
    if not hasattr(_LOCAL, "session"):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers.update({"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, text/html;q=0.8"})
    # O limite vale durante a leitura, antes de alocar um corpo arbitrariamente grande.
    with _LOCAL.session.get(url, timeout=timeout, stream=True) as r:
        partes, total = [], 0
        for bloco in r.iter_content(65536):
            total += len(bloco)
            if total > 8_000_000:
                raise requests.RequestException("resposta acima de 8 MB")
            partes.append(bloco)
        r._content = b"".join(partes)
        r._content_consumed = True
        return r


def baixar_feed(url: str, timeout: int = TIMEOUT):
    """Baixa e valida um feed. Devolve o objeto do feedparser ou None."""
    try:
        r = _get(url, timeout)
        if not r.ok:
            return None
        d = feedparser.parse(r.content)
        return d if d.entries else None
    except requests.RequestException:
        return None


def descobrir_feed(site):
    """Procura o feed do veículo: <link rel=alternate> na home e caminhos comuns."""
    candidatos: list[str] = []
    try:
        r = _get(site, TIMEOUT_SONDA)
        if r.ok:
            parsed = feedparser.parse(r.content)
            if parsed.entries:
                return r.url, parsed
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
        parsed = baixar_feed(u, TIMEOUT_SONDA)
        if parsed:
            return u, parsed
    return None, None


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
        try:
            iso = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
            if iso.tzinfo:
                return iso.astimezone(timezone.utc)
        except ValueError:
            pass
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


def extrair_itens(veiculo, feed, agora):
    """Classifica até 100 entradas ANTES de limitar; mantém geral por 36h."""
    itens, vistos = [], set()
    for e in feed.entries[:100]:
        titulo = limpar_texto(e.get("title"))
        link = url_limpa(e.get("link") or "")
        quando = data_item(e)
        if (len(titulo) < 12 or not link or not quando or quando > agora + timedelta(hours=6)
                or link in vistos or titulo.casefold() in vistos):
            continue
        descricao = limpar_texto(e.get("summary", ""))[:1600]
        categorias = " ".join(t.get("term", "") for t in e.get("tags", []))
        tema, relevancia, motivos = classificar(titulo, descricao, categorias, veiculo.get("tema", ""))
        idade = (agora - quando).total_seconds() / 3600
        geral = idade <= 36
        if tema and idade > POR_ID[tema]["dias"] * 24:
            tema = ""
        if not tema and not geral:
            continue
        vistos.update([link, titulo.casefold()])
        itens.append(dict(veiculo=veiculo["nome"], site=veiculo["site"], pais=veiculo["pais"],
                          uf=veiculo.get("uf", ""), titulo=titulo, url=link, tema=tema,
                          publicado=quando.astimezone(FUSO).strftime("%d/%m/%Y %H:%M"),
                          timestamp=quando.timestamp(), relevancia=relevancia, motivos=motivos,
                          geral=geral, tipo=tipo_publicacao(titulo, tema),
                          natureza=veiculo.get("tipo", "jornalismo")))
    # Janela e ordem explícitas: o início do RSS não consome a cota temática.
    itens.sort(key=lambda i: (-i["relevancia"], -i["timestamp"], i["url"]))
    gerais = {i["url"] for i in sorted((i for i in itens if i["geral"]),
              key=lambda i: -i["timestamp"])[:4]}
    contagem, selecionados = Counter(), []
    for i in itens:
        tematico = i["tema"] and contagem[i["tema"]] < 8
        if tematico or i["url"] in gerais:
            selecionados.append(i)
            if tematico:
                contagem[i["tema"]] += 1
    return selecionados


def processar_veiculo(veiculo: dict, entrada_cache: dict | None, agora: datetime):
    feed_url = veiculo.get("feed") or (entrada_cache or {}).get("feed")
    d = baixar_feed(feed_url) if feed_url else None
    if not d:
        if (not veiculo.get("feed") and entrada_cache and not entrada_cache.get("feed")
                and entrada_cache.get("checado_em")):
            idade = agora - datetime.fromisoformat(entrada_cache["checado_em"])
            if idade < timedelta(days=REVERIFICAR_DIAS):
                return None, [], "sem feed (verificado recentemente)"
        # Fontes especializadas usam endereços explícitos: não sondar portais
        # institucionais inteiros se um endpoint estiver temporariamente indisponível.
        if veiculo.get("tema") and veiculo.get("feed"):
            return feed_url, [], "RSS especializado indisponível"
        feed_url, d = descobrir_feed(veiculo["site"])
    if not d:
        return None, [], "feed não encontrado ou fora do ar"
    itens = extrair_itens(veiculo, d, agora)
    if not itens:
        return feed_url, [], "feed sem itens recentes"
    return feed_url, itens, None


def coletar(veiculos: list[dict], agora: datetime):
    caminho_cache = RAIZ / "data" / "feeds_cache.json"
    try:
        cache = json.loads(caminho_cache.read_text("utf-8")) if caminho_cache.exists() else {}
    except (ValueError, OSError):
        cache = {}
    sites = {v["site"] for v in veiculos}
    cache = {k: v for k, v in cache.items() if k in sites}
    itens, falhas, com_itens = [], [], 0
    with cf.ThreadPoolExecutor(TRABALHADORES) as ex:
        futuros = {ex.submit(processar_veiculo, v, cache.get(v["site"]), agora): v for v in veiculos}
        for concluidos, fut in enumerate(cf.as_completed(futuros), 1):
            v = futuros[fut]
            if concluidos % 100 == 0 or concluidos == len(veiculos):
                log(f"Coleta: {concluidos}/{len(veiculos)} fontes processadas")
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
    salvar_json(caminho_cache, cache)
    itens.sort(key=lambda i: (i["veiculo"], i["url"]))
    return itens, {"consultados": len(veiculos), "com_manchetes": com_itens, "falhas": sorted(falhas, key=lambda x: x["veiculo"])}


def carregar_demo(veiculos, agora):
    """Fixtures explicitamente fictícias, percorrendo o mesmo classificador."""
    por_nome = {v["nome"]: v for v in veiculos}
    bruto = json.loads((RAIZ / "tests" / "amostra.json").read_text("utf-8"))
    itens = []
    for n, i in enumerate(bruto):
        v = por_nome.get(i["veiculo"])
        if v:
            e = dict(title=i["titulo"], summary=i.get("descricao", ""),
                     link=v["site"].rstrip("/") + "/exemplo-" + str(n),
                     published=agora.isoformat())
            itens.extend(extrair_itens(v, SimpleNamespace(entries=[e]), agora))
    return itens, {"consultados":len(veiculos), "com_manchetes":len({i["veiculo"] for i in itens}), "falhas":[]}


def montar_gerais(itens, veiculos, falhas):
    secoes = []
    for slug, nome, br in [("brasil", "Brasil", True), ("mundo", "Resto do mundo", False)]:
        fontes = [v for v in veiculos if (v["pais"] == "Brasil") == br]
        recentes = sorted((i for i in itens if i["geral"] and (i["pais"] == "Brasil") == br),
                          key=lambda i: (-i["timestamp"], i["url"]))
        por_fonte, selecionados = Counter(), []
        for i in recentes:
            if por_fonte[i["veiculo"]] < 4:
                selecionados.append(i)
                por_fonte[i["veiculo"]] += 1
        nomes = {v["nome"] for v in fontes}
        cobertura = [
            dict(uf=uf, fontes=[v for v in fontes if v.get("uf") == uf])
            for uf in sorted({v.get("uf") for v in fontes if v.get("uf")})]
        secoes.append(dict(id=slug, nome=nome, itens=selecionados, cobertura=cobertura,
                           consultados=len(fontes), falhas=[f for f in falhas if f["veiculo"] in nomes]))
    return secoes


TEMPLATES = Environment(loader=FileSystemLoader(RAIZ / "templates"),
                        autoescape=select_autoescape(["html"]))


def renderizar(contexto, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(TEMPLATES.get_template("capa.html").render(**contexto), "utf-8")


def publicar_paginas(itens, veiculos, status, agora, saida, demo):
    editorias = montar_editorias(itens)
    contexto = dict(editorias=editorias, grupos=[], arquivo=[], demo=demo, pagina="capa",
                    titulo="Seus interesses, em foco.", base="",
                    atualizacao=agora.strftime("%d/%m/%Y às %H:%M"),
                    total_fontes=len(veiculos), fontes_ativas=status["com_manchetes"],
                    ufs=sorted({v.get("uf") for v in veiculos if v.get("uf")}))
    renderizar(contexto, saida / "index.html")
    for e in editorias:
        renderizar(dict(contexto, editorias=[e], pagina="editoria", titulo=e["nome"], base="../"),
                   saida / "editorias" / (e["id"] + ".html"))
    renderizar(dict(contexto, pagina="gerais", titulo="Brasil e resto do mundo",
                    grupos=montar_gerais(itens, veiculos, status["falhas"])),
               saida / "gerais.html")
    if not demo:
        renderizar(dict(contexto, base="../"), saida / "edicoes" / (agora.date().isoformat() + ".html"))
        edicoes = sorted((saida / "edicoes").glob("????-??-??.html"), reverse=True)
        for antiga in edicoes[90:]:
            antiga.unlink()
        renderizar(dict(contexto, pagina="arquivo", titulo="Edições anteriores", base="../",
                        arquivo=[e.stem for e in edicoes[:90]]),
                   saida / "edicoes" / "index.html")
    return editorias


def principal():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="gera prévia fictícia, sem rede")
    ap.add_argument("--max-veiculos", type=int, help="limita fontes para diagnóstico")
    ap.add_argument("--saida", default=str(RAIZ / "docs"))
    ap.add_argument("--min-veiculos", type=int, default=20)
    args = ap.parse_args()
    if args.min_veiculos < 1 or (args.max_veiculos is not None and args.max_veiculos < 1):
        ap.error("limites de veículos devem ser positivos")
    agora = datetime.now(FUSO)
    veiculos = carregar_veiculos()
    if args.max_veiculos:
        veiculos = veiculos[:args.max_veiculos]
    itens, status = (carregar_demo if args.demo else coletar)(veiculos, agora.astimezone(timezone.utc))
    diagnostico = dict(status, gerada_em=agora.isoformat())
    if not args.demo:
        salvar_json(RAIZ / "data" / "ultima_tentativa.json", diagnostico)
    if not itens or (not args.demo and (status["com_manchetes"] < args.min_veiculos
                                      or not any(i.get("tema") for i in itens))):
        log("Cobertura insuficiente: edição anterior preservada.")
        return 1
    # Gera tudo antes de substituir arquivos da edição pública.
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capa-") as pasta:
        staging = Path(pasta)
        if (saida / "edicoes").exists():
            shutil.copytree(saida / "edicoes", staging / "edicoes")
        editorias = publicar_paginas(itens, veiculos, status, agora, staging, args.demo)
        for arquivo in staging.rglob("*.html"):
            destino = saida / arquivo.relative_to(staging)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(arquivo, destino.with_suffix(".tmp"))
            destino.with_suffix(".tmp").replace(destino)
        if not args.demo:
            for antigo in (saida / "edicoes").glob("????-??-??.html"):
                if not (staging / "edicoes" / antigo.name).exists():
                    antigo.unlink()
    if not args.demo:
        salvar_json(RAIZ / "data" / "ultima_execucao.json",
                    dict(diagnostico, manchetes=len(itens), editorias=[
                        dict(id=e["id"], total=e["total"], destaques=len(e["destaques"]))
                        for e in editorias]))
    log(f"Geradas cinco editorias: {sum(len(e['destaques']) for e in editorias)} destaques; "
        f"{status['com_manchetes']}/{len(veiculos)} fontes com notícias.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
