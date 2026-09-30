"""Testes rápidos (sem rede):  python -m pytest tests  ou  python tests/test_capa.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import capa


def test_url_limpa_remove_rastreio_e_esquemas_invalidos():
    assert capa.url_limpa("https://x.com/a?utm_source=t&id=3#topo") == "https://x.com/a?id=3"
    assert capa.url_limpa("javascript:alert(1)") is None
    assert capa.url_limpa("ftp://x.com/a") is None


def test_limpar_texto_tira_html_e_entidades():
    assert capa.limpar_texto("<b>Alta&nbsp;do d&oacute;lar</b>  hoje") == "Alta do dólar hoje"


def test_agrupamento_junta_o_mesmo_assunto_e_separa_o_resto():
    titulos = [
        "Banco Central mantém taxa de juros e sinaliza cautela",
        "Banco Central mantém juros e sinaliza cautela com inflação",
        "Terremoto de magnitude 6,8 atinge região costeira",
        "Terremoto de 6,8 atinge a costa; sem alerta de tsunami",
        "Escolas testam calendário com semana de quatro dias",
    ]
    grupos, _, _, _ = capa.agrupar(titulos, "tfidf", capa.LIMIAR_PADRAO["tfidf"])
    conjuntos = sorted(sorted(g) for g in grupos)
    assert conjuntos == [[0, 1], [2, 3], [4]]


def test_grupo_de_um_unico_veiculo_nao_vira_historia():
    itens = [
        {"veiculo": "A", "titulo": "Chuva forte alaga ruas", "titulo_exibicao": "Chuva forte alaga ruas", "url": "https://a/1",
         "regiao": "Europa", "site": "https://a", "publicado": None},
        {"veiculo": "A", "titulo": "Chuva forte alaga ruas da capital", "titulo_exibicao": "Chuva forte alaga ruas da capital",
         "url": "https://a/2", "regiao": "Europa", "site": "https://a", "publicado": None},
    ]
    grupos, sim, _, _ = capa.agrupar([i["titulo_exibicao"] for i in itens], "tfidf", 0.4)
    historias, consumidos = capa.montar_historias(itens, grupos, sim)
    assert historias == [] and consumidos == set()


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn(); print("ok", nome)


def test_datas_portuguesas_e_itens_sem_data():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    agora = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)
    v = dict(nome='Teste', site='https://example.com', pais='Brasil', regiao='América Latina')
    titulo = 'Uma manchete suficientemente longa'
    feed = SimpleNamespace(entries=[
        dict(title=titulo, link='https://example.com/recente', published='Ter, 29 Set 2026 11:04:05 -0300'),
        dict(title=titulo+' antiga', link='https://example.com/antiga', published='Mon, 20 Oct 2022 16:00:00 +0000'),
        dict(title=titulo+' sem data', link='https://example.com/sem-data'),
        dict(title=titulo+' futura', link='https://example.com/futura', published='Wed, 30 Sep 2026 16:00:00 +0000'),
    ])
    itens = capa.extrair_itens(v, feed, agora)
    assert [i['url'] for i in itens] == ['https://example.com/recente']
    assert itens[0]['publicado'] == '2026-09-29T14:04:05+00:00'


def test_cobertura_insuficiente_preserva_edicao(monkeypatch, tmp_path):
    import sys
    saida = tmp_path / 'docs'
    saida.mkdir()
    (saida / 'index.html').write_text('edição anterior')
    monkeypatch.setattr(capa, 'RAIZ', tmp_path)
    monkeypatch.setattr(capa, 'carregar_veiculos', lambda: [{}] * 40)
    monkeypatch.setattr(capa, 'coletar', lambda *a: ([{'titulo':'notícia'}], {'consultados':40,'com_manchetes':1,'falhas':[]}))
    monkeypatch.setattr(sys, 'argv', ['capa.py','--saida',str(saida)])
    assert capa.principal() == 1
    assert (saida / 'index.html').read_text() == 'edição anterior'
    assert (tmp_path / 'data' / 'ultima_tentativa.json').exists()


def test_demo_funciona_com_lista_parcial(monkeypatch):
    from datetime import datetime, timezone
    selecionados = capa.carregar_veiculos()[:5]
    itens, status = capa.carregar_demo(selecionados, datetime.now(timezone.utc))
    assert status['consultados'] == 5
    assert all(i['veiculo'] in {v['nome'] for v in selecionados} for i in itens)


def test_fontes_incluem_lista_original_sem_duplicatas():
    import csv
    fontes = capa.carregar_veiculos()
    with (capa.RAIZ / 'outlets-completo.csv').open() as f:
        originais = list(csv.DictReader(f))
    sites = {v['site'] for v in fontes}
    assert len(sites) == len(fontes)
    assert len(fontes) >= 436
    assert {v['site'] for v in originais} <= sites
    assert all(capa.url_limpa(v['site']) for v in fontes)
    assert all(not v['feed'] or capa.url_limpa(v['feed']) for v in fontes)
    assert sum(bool(v['feed']) for v in fontes) >= 40


def test_cobertura_brasil_todas_ufs():
    fontes = [v for v in capa.carregar_veiculos() if v['pais'] == 'Brasil']
    ufs = set('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())
    assert {v['uf'] for v in fontes if v.get('uf')} == ufs
    assert {v['regiao_br'] for v in fontes if v.get('regiao_br')} == {'Norte','Nordeste','Centro-Oeste','Sudeste','Sul'}


def test_grupos_separam_pelo_pais_e_nao_pelo_idioma():
    titulo = 'Banco Central mantém taxa de juros e sinaliza cautela'
    fontes = [dict(nome='Fonte BR', pais='Brasil', site='https://br.example', uf='RS', regiao_br='Sul'),
              dict(nome='Fonte estrangeira em português', pais='Portugal', site='https://pt.example')]
    itens = [dict(veiculo=v['nome'], pais=v['pais'], site=v['site'], regiao='Europa',
                  titulo=titulo, titulo_exibicao=titulo, url=v['site']+'/noticia', publicado='2026-09-30T10:00:00+00:00') for v in fontes]
    secoes, historias = capa.montar_secoes(itens, fontes, [], 'tfidf', .36)
    assert [s['nome'] for s in secoes] == ['Brasil','Resto do mundo']
    assert [s['manchetes'] for s in secoes] == [1,1]
    assert historias == [] # títulos iguais em grupos distintos não se misturam
    assert secoes[0]['destaque']['fontes'][0]['veiculo'] == 'Fonte BR'
    assert secoes[1]['destaque']['fontes'][0]['veiculo'] == 'Fonte estrangeira em português'
    assert secoes[0]['cobertura'][0]['uf'] == 'RS'
    assert secoes[0]['cobertura'][0]['manchetes'] == 1


def test_grupo_vazio_e_falhas_ficam_no_grupo_correto():
    fontes = [dict(nome='Fonte BR', pais='Brasil', site='https://br.example')]
    secoes, historias = capa.montar_secoes([], fontes, [{'veiculo':'Fonte BR','motivo':'sem notícias'}], 'tfidf', .36)
    assert historias == []
    assert all(s['destaque'] is None for s in secoes)
    assert len(secoes[0]['falhas']) == 1
    assert secoes[1]['falhas'] == []
