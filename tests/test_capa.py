import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capa
from editorial import classificar, mesmo_assunto, montar_editorias, tipo_publicacao

AGORA = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
FONTE = dict(nome='Fonte', site='https://example.org', pais='Brasil', uf='SP')


def entry(title, n=0, days=0):
    return dict(title=title, link=f'https://example.org/{n}',
                published=(AGORA-timedelta(days=days)).isoformat())


@pytest.mark.parametrize('title,theme', [
    ('Novo software aumenta segurança digital','tecnologia'),
    ('OpenAI apresenta modelo de inteligência artificial','ia'),
    ('AI improves research','ia'),
    ('Cientistas estudam biodiversidade','ciencia'),
    ('IBS e CBS: nova proposta para reforma tributária','tributaria'),
    ('STJ decide sobre responsabilidade civil','juridico'),
    ('STJ nega habeas corpus a preso por roubo',''),
    ('Tecnologia ajuda polícia após assassinato',''),
    ('Corrupção e propina são investigadas pelo STF',''),
    ('LGPD: responsabilidade civil após roubo de dados','juridico'),
    ('Clube anuncia contratação para campeonato',''),
    ('CBS News announces new programming',''),
    ('CBS terá novas regras para contribuintes','tributaria'),
    ('Pesquisadores apontam risco à democracia se candidato for eleito',''),
    ('Relator do STJ vota sobre acusado de ajudar em morte de família',''),
    ('Contratos entram na mira do MP por suspeitas em licitação',''),
])
def test_classificar(title, theme):
    assert classificar(title)[0] == theme


def test_limites_de_palavras_e_fonte():
    assert classificar('Saia de praia é tendência')[0] == ''
    assert classificar('Evento reúne servidores', especialidade='juridico')[0] == ''
    assert classificar('New discovery announced', especialidade='ciencia')[0] == 'ciencia'


def test_classifica_antes_de_limitar():
    entries = [entry('Polícia investiga roubo no centro', i) for i in range(8)]
    entries.append(entry('Guia da reforma tributária e IBS', 9))
    itens = capa.extrair_itens(FONTE, SimpleNamespace(entries=entries), AGORA)
    assert any(i['tema']=='tributaria' for i in itens)
    assert len(itens) <= 5


def test_janelas_e_datas_invalidas():
    entries = [entry('Reforma tributária: novos créditos',1,10),
               entry('Software recebe atualização importante',2,5),
               entry('STJ decide sobre contratos antigos',3,15),
               entry('Inteligência artificial para pesquisa',4,-1),
               dict(title='Cientistas estudam biodiversidade', link='https://example.org/5')]
    itens=capa.extrair_itens(FONTE,SimpleNamespace(entries=entries),AGORA)
    assert len(itens)==1 and itens[0]['tema']=='tributaria'
    assert not itens[0]['geral']


def test_rotulos_nao_confirmam_vigencia():
    assert tipo_publicacao('Proposta de lei altera IBS','tributaria')=='Proposta / discussão'
    assert tipo_publicacao('STJ decide sobre contratos','juridico')=='Decisão / julgamento'
    assert tipo_publicacao('Publicada portaria sobre CBS','tributaria')=='Publicação normativa'
    assert tipo_publicacao('IBS em debate','tributaria')=='Notícia / análise'


def test_diversidade_limite_e_duplicacao():
    itens=[]
    for n in range(20):
        itens.append(dict(tema='ia',titulo=f'Modelo de inteligência artificial {n}',
                          relevancia=10,timestamp=n,url=f'https://example.org/{n}',veiculo=f'Fonte {n//4}'))
    itens.append(dict(itens[0],url='https://another.org/0',veiculo='Outra fonte'))
    e=next(e for e in montar_editorias(itens) if e['id']=='ia')
    assert len(e['destaques'])==7
    assert max(Counter(i['veiculo'] for i in e['destaques']).values())<=2
    assert e['total']==20
    assert sum(len(i['relacionadas']) for i in e['destaques']+e['demais'])==1
    assert not mesmo_assunto('tribunal aprova credito 10','tribunal nega credito 10')
    assert not mesmo_assunto('tributo sobe 10','tributo sobe 20')


def test_fontes_regionais_preservadas():
    fontes=capa.carregar_veiculos()
    estados=Counter(v.get('uf') for v in fontes if v.get('uf'))
    assert len(estados)==27
    assert min(estados.values())>=10
    assert len({v['site'].rstrip('/') for v in fontes})==len(fontes)


def test_urls_e_datas():
    assert capa.url_limpa('javascript:alert(1)') is None
    assert capa.url_limpa('https://example.org/a?utm_source=x&id=3#x')=='https://example.org/a?id=3'
    assert capa.data_item(dict(published='22 out 2026 09:00:00 -0300'))==AGORA


def test_feed_especializado_nao_sonda_portal(monkeypatch):
    monkeypatch.setattr(capa,'baixar_feed',lambda *a:None)
    def unexpected(*a):
        raise AssertionError('sondagem desnecessária')
    monkeypatch.setattr(capa,'descobrir_feed',unexpected)
    _,itens,erro=capa.processar_veiculo(dict(FONTE,tema='ia',feed='https://example.org/rss'),None,AGORA)
    assert not itens and erro


def test_renderizacao_e_gerais(tmp_path):
    fontes=capa.carregar_veiculos()
    itens,status=capa.carregar_demo(fontes,AGORA)
    itens[0]['titulo']='<script>alert(1)</script> software'
    capa.publicar_paginas(itens,fontes,status,AGORA,tmp_path,False)
    soup=BeautifulSoup((tmp_path/'index.html').read_text(),'html.parser')
    assert len(soup.select('section.section'))==5
    assert len(soup.select('article.card'))<=30
    assert '<script>alert(1)</script>' not in str(soup)
    assert 'roubo no centro' not in soup.get_text()
    assert 'roubo no centro' in (tmp_path/'gerais.html').read_text()
    assert len(list((tmp_path/'editorias').glob('*.html')))==5
    assert (tmp_path/'edicoes/2026-10-22.html').exists()
    assert (tmp_path/'edicoes/index.html').exists()
    for a in soup.select('a[href]'):
        href=a['href']
        if not href.startswith('http'):
            assert (tmp_path/href).exists(),href


def test_coleta_insuficiente_preserva_edicao(tmp_path,monkeypatch):
    (tmp_path/'index.html').write_text('EDIÇÃO ANTERIOR')
    monkeypatch.setattr(capa,'carregar_veiculos',lambda:[FONTE])
    monkeypatch.setattr(capa,'coletar',lambda *a:([],dict(consultados=1,com_manchetes=0,falhas=[])))
    monkeypatch.setattr(capa,'salvar_json',lambda *a:None)
    monkeypatch.setattr(sys,'argv',['capa.py','--saida',str(tmp_path)])
    assert capa.principal()==1
    assert (tmp_path/'index.html').read_text()=='EDIÇÃO ANTERIOR'


def test_erro_na_renderizacao_preserva_edicao(tmp_path, monkeypatch):
    fontes=capa.carregar_veiculos()
    itens,status=capa.carregar_demo(fontes,AGORA)
    (tmp_path/'index.html').write_text('EDIÇÃO ANTERIOR')
    monkeypatch.setattr(capa,'carregar_veiculos',lambda:fontes)
    monkeypatch.setattr(capa,'carregar_demo',lambda *a:(itens,status))
    def falhar(*a):
        raise RuntimeError('erro simulado')
    monkeypatch.setattr(capa,'publicar_paginas',falhar)
    monkeypatch.setattr(sys,'argv',['capa.py','--demo','--saida',str(tmp_path)])
    with pytest.raises(RuntimeError):
        capa.principal()
    assert (tmp_path/'index.html').read_text()=='EDIÇÃO ANTERIOR'
