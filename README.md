# Capa do Mundo

Agregador de títulos e links, com capa temática e notícias gerais separadas.
Site: https://mac-manito.github.io/Repo/

## Capa temática

| Editoria | Destaques máximos | Janela de publicação |
|---|---:|---:|
| Tecnologia | 5 | 3 dias |
| Inteligência artificial | 7 | 7 dias |
| Ciência | 5 | 7 dias |
| Tributária e reforma tributária | 8 | 14 dias |
| Jurídico | 5 | 14 dias |

Cada editoria tem sua própria página com as demais notícias selecionadas. A capa
limita a dois destaques por fonte em cada editoria. Quando faltam notícias, exibe
menos destaques; nunca inventa conteúdo para preencher espaços.

Notícias gerais ficam em `gerais.html`, em dois grupos: Brasil e resto do mundo.
Mantivemos as 679 fontes anteriores, incluindo dez por estado brasileiro e Distrito
Federal. Doze fontes especializadas foram acrescentadas, totalizando 691 fontes.
A lista por UF continua disponível nessa página e em [FONTES_BRASIL.md](FONTES_BRASIL.md).
Cadastro não garante publicação diária nem disponibilidade do RSS.

Busca e filtros por origem e UF atuam nas notícias da página aberta. A localização
é a da fonte principal, não a do acontecimento. Fontes nacionais não recebem UF.

## Seleção e limites

1. Consulta RSS/Atom públicos, com até 24 trabalhadores, sessões HTTP reutilizadas,
   timeout e limite de resposta de 8 MB. Feeds descobertos ficam em cache;
   fontes sem feed são verificadas novamente após sete dias.
2. Examina até 100 entradas por feed e classifica antes de aplicar limites.
   Datas ausentes, inválidas ou mais de seis horas no futuro são descartadas.
3. Usa termos configuráveis em português e inglês. O título tem mais peso que
   descrição/categoria; especialização da fonte apoia tecnologia, IA e ciência.
   Fontes jurídicas e tributárias exigem correspondência temática explícita.
4. Reduz conteúdo policial/penal e corrupção na capa, mantendo exceções úteis
   como LGPD, cibersegurança e reforma tributária. Notícias gerais não recebem
   esse filtro. Regras podem errar e não cobrem todos os idiomas.
5. Retém até oito candidatos por tema/fonte, além das quatro notícias gerais
   mais recentes das últimas 36 horas. Ordena por relevância temática e depois
   data. Agrupa URLs iguais e títulos muito semelhantes, com proteção para
   números e negações diferentes. Não garante detectar toda repetição.
6. Exige pelo menos 20 fontes com notícias e algum conteúdo temático para
   publicar. Gera todas as páginas antes de substituir arquivos. O workflow
   só publica se geração e testes passarem, preservando o site em caso de falha.

Fontes de empresas e instituições são identificadas. Rótulos jurídicos distinguem
proposta/discussão, decisão/julgamento, publicação normativa, orientação e notícia/análise
com base no título. **Não confirmam vigência, efeito vinculante ou aplicabilidade**;
consulte a publicação e o documento original.

Sem API de IA, modelos baixados, tradução automática ou resumos gerados. A aplicação
mostra títulos no idioma original e links; não contorna paywalls. O agrupamento usa
apenas a biblioteca padrão do Python, sem matriz global de similaridade.

## Atualizar no GitHub

Abra **Actions → Capa diária → Run workflow → Run workflow** e aguarde `gerar` e
`publicar`. Também há execução agendada para 7h de Brasília (10h UTC), sujeita à fila
e à disponibilidade do GitHub. Em Settings → Pages, a origem deve ser GitHub Actions.
A publicação é pública. Não há servidor contratado nem chave de API; aplicam-se
as regras e limites dos serviços utilizados.

## Configuração

- `outlets.csv`: cadastro geral preservado, incluindo fontes por UF.
- `fontes_tematicas.json`: fontes especializadas e especialidade das já cadastradas.
- `editorias.json`: nomes, limites, janelas e palavras-chave das cinco editorias.
- `editorial.py`: classificação, rótulos e seleção dos destaques.
- `capa.py`: coleta, validação de datas, cache e geração das páginas.
- `templates/capa.html`: layout responsivo e filtros locais, sem bibliotecas JavaScript.
- `outlets-completo.csv`: cadastro histórico de referência, preservado.

Para acrescentar uma fonte especializada, cadastre nome, site, país, tema, tipo
(`jornalismo`, `empresa`, `institucional` ou `oficial`) e feed validado no JSON.
Sites já presentes no CSV recebem os metadados e o feed explícito, quando informado, sem duplicação.

## Executar localmente

Python 3.12:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python capa.py
python -m http.server 8000 --directory docs
```

Abra http://localhost:8000. Prévia fictícia, sem rede:

```sh
python capa.py --demo --saida /tmp/capa-demo
```

Testes:

```sh
python -m pip install pytest==9.1.1
python -m pytest tests -q
```

## Diagnóstico e histórico

`data/ultima_execucao.json` registra cobertura e quantidade por editoria;
`data/ultima_tentativa.json` registra a tentativa, mesmo quando insuficiente.
`data/feeds_cache.json` armazena os feeds descobertos. O artefato `diagnostico`
fica disponível em Actions por sete dias. As últimas 90 edições diárias ficam
em `docs/edicoes`; edições anteriores à mudança conservam seu formato original.
Os links de navegação das edições históricas levam às editorias atuais.

Os workflows mantidos têm funções distintas: testes de pull requests e
coleta/publicação diária. Não há workflow separado ou serviço para IA.
