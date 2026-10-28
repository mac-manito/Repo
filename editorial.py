"""Classificação explicável e seleção limitada por tema, sem modelos externos."""
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

EDITORIAS = json.loads(Path(__file__).with_name("editorias.json").read_text("utf-8"))
POR_ID = {e["id"]: e for e in EDITORIAS}


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                   if not unicodedata.combining(c))


def padrao(termos):
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in termos) + r")(?!\w)")


REGRAS = {e["id"]: padrao(e["termos"]) for e in EDITORIAS}
POLICIAL = padrao(["assassinato", "assassinado", "assassinada", "homicidio", "latrocinio",
                  "roubo", "furto", "baleado", "baleada", "traficante", "trafico de drogas",
                  "estupro", "feminicidio", "preso", "presa", "apreensao", "contrabando",
                  "corrupcao", "propina", "homicidios", "assassinatos", "roubos", "furtos",
                  "trafico", "traficantes", "presos", "presas", "sequestro",
                  "murder", "robbery", "arrested", "homicide", "shooting", "corruption"])
CONTEXTO_UTIL = padrao(["reforma tributaria", "obrigacoes acessorias", "credito tributario",
                        "execucao fiscal", "lgpd", "ciberseguranca", "ransomware",
                        "protecao de dados", "vazamento de dados", "responsabilidade civil"])
PENAL = padrao(["habeas corpus", "prisao", "pena criminal", "direito penal",
               "condenacao criminal", "inquerito policial", "dosimetria"])
PRIORIDADE = {"tributaria":5, "ia":4, "juridico":3, "ciencia":2, "tecnologia":1}
TERMOS_GENERICOS = {"stj", "stf", "tst", "supreme court", "pesquisadores", "researchers"}
ESCANDALO = re.compile(r"\b(?:acusad[oa]s?|suspeit[oa]s?|escandalo|investigacoes|entra[m]? na mira)\b|aciona .+ investigar")


def classificar(titulo, descricao="", categorias="", especialidade=""):
    principal = normalizar(titulo)
    if (POLICIAL.search(principal) or PENAL.search(principal) or ESCANDALO.search(principal)) and not CONTEXTO_UTIL.search(principal):
        return "", 0, []
    apoio = normalizar(descricao[:1600] + " " + categorias)
    pontos, motivos = {}, {}
    for tema, regex in REGRAS.items():
        fortes = set(regex.findall(principal))
        fracos = set(regex.findall(apoio))
        # CBS também é uma emissora americana, sem relação com tributação.
        if tema == "tributaria" and not re.search(r"tribut|\bibs\b|impost|contribui|fiscal|consumo", principal + " " + apoio):
            fortes.discard("cbs")
            fracos.discard("cbs")
        pontos[tema] = sum(1 if t in TERMOS_GENERICOS else 4 for t in fortes) + min(2, len(fracos - TERMOS_GENERICOS))
        motivos[tema] = sorted(fortes | fracos)
    if re.search(r"\b(?:IA|AI)\b", titulo):
        pontos["ia"] += 4
        motivos["ia"].append("IA/AI no título")
    # Uma fonte especializada apoia a classificação; tribunais e órgãos fiscais
    # continuam exigindo assunto explícito para evitar notícias policiais/institucionais.
    if especialidade in ("tecnologia", "ia", "ciencia"):
        pontos[especialidade] += 3
        motivos[especialidade].append("fonte especializada")
    tema = max(pontos, key=lambda t: (pontos[t], PRIORIDADE[t]))
    return (tema, pontos[tema], motivos[tema]) if pontos[tema] >= 3 else ("", 0, [])


def tipo_publicacao(titulo, tema):
    if tema not in ("tributaria", "juridico"):
        return ""
    t = normalizar(titulo)
    if re.search(r"\b(projeto de lei|proposta|propoe|consulta publica|plp)\b", t):
        return "Proposta / discussão"
    if re.search(r"\b(julga|julgamento|decide|decisao|acordao|jurisprudencia|sumula)\b", t):
        return "Decisão / julgamento"
    if re.search(r"\b(publica|publicada|publicado|regulamenta)\b", t) and re.search(
            r"\b(lei|decreto|portaria|instrucao normativa|resolucao)\b", t):
        return "Publicação normativa"
    if re.search(r"\b(orienta|orientacao|orientacoes|manual|guia|esclarece)\b", t):
        return "Orientação"
    return "Notícia / análise"


def chave_titulo(titulo):
    return " ".join(re.findall(r"\w+", normalizar(titulo)))


def mesmo_assunto(a, b):
    # Conservador: evita fundir decisões opostas ou valores/datas diferentes.
    if a == b:
        return True
    if set(re.findall(r"\d+", a)) != set(re.findall(r"\d+", b)):
        return False
    negacao = {"nao", "not", "nega", "rejeita", "aprova", "mantem", "derruba"}
    if (set(a.split()) & negacao) != (set(b.split()) & negacao):
        return False
    ta, tb = set(a.split()), set(b.split())
    return (len(ta & tb) / max(1, len(ta | tb)) >= .78
            and SequenceMatcher(None, a, b, autojunk=False).ratio() >= .86)


def montar_editorias(itens):
    secoes = []
    for config in EDITORIAS:
        candidatos = [i for i in itens if i.get("tema") == config["id"]]
        candidatos.sort(key=lambda i: (-i["relevancia"], -i["timestamp"], i["url"]))
        grupos, indice_url, indice_titulo = [], {}, {}
        # O índice por palavra longa reduz comparações sem matriz quadrática global.
        indice_palavra = {}
        for i in candidatos:
            chave = chave_titulo(i["titulo"])
            palavras = {p for p in chave.split() if len(p) >= 5}
            possiveis = set().union(*(indice_palavra.get(p, set()) for p in palavras)) if palavras else set()
            grupo = indice_url.get(i["url"], indice_titulo.get(chave))
            if grupo is None:
                grupo = next((n for n in sorted(possiveis) if mesmo_assunto(chave, grupos[n]["chave"])), None)
            if grupo is None:
                grupo = len(grupos)
                grupos.append(dict(i, chave=chave, relacionadas=[]))
                for p in palavras:
                    indice_palavra.setdefault(p, set()).add(grupo)
            elif i["veiculo"] != grupos[grupo]["veiculo"]:
                if i["veiculo"] not in {f["veiculo"] for f in grupos[grupo]["relacionadas"]}:
                    grupos[grupo]["relacionadas"].append(i)
            indice_url[i["url"]] = grupo
            indice_titulo[chave] = grupo
        contagem, destaques, demais = Counter(), [], []
        for g in grupos:
            if len(destaques) < config["limite"] and contagem[g["veiculo"]] < 2:
                destaques.append(g)
                contagem[g["veiculo"]] += 1
            else:
                demais.append(g)
        secoes.append(dict(config, destaques=destaques, demais=demais, total=len(grupos)))
    return secoes
