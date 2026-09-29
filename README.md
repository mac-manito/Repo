# Capa do Mundo

Agregador pessoal de manchetes de **20 fontes brasileiras e 20 dos Estados Unidos**.
Página pública, responsiva, com busca, agrupamento de assuntos e histórico das últimas 90 edições.

## Colocar no ar — uma configuração

1. Neste repositório, abra **Settings → Pages**.
2. Em **Build and deployment → Source**, selecione **GitHub Actions**.
3. Abra **Actions → Capa diária → Run workflow → Run workflow**.
4. Aguarde os trabalhos `gerar` e `publicar` terminarem. O endereço previsto é:
   **https://mac-manito.github.io/Repo/**.

A execução também ocorre diariamente às **7h de Brasília (10h UTC)**, sujeita a atrasos
na fila do GitHub. O workflow precisa permanecer habilitado. O GitHub pode desabilitar
agendamentos após períodos prolongados sem atividade; nesse caso, reative em Actions.

## Custo

Esta configuração usa GitHub Pages e executores Linux padrão do GitHub Actions em
repositório público. Não usa API paga, servidor contratado, domínio próprio ou chave de IA.
As regras e limites dos serviços se aplicam. Os textos completos dos veículos podem ter paywall;
o app mostra apenas títulos e links e não contorna assinaturas.

## Como funciona

- Consulta feeds RSS/Atom públicos configurados em `outlets.csv`.
- Aceita até quatro manchetes por fonte, com publicação nas últimas 36 horas.
- Exclui itens sem data interpretável ou mais de seis horas no futuro.
- Usa TF-IDF local para agrupar títulos semelhantes. Não traduz: português e inglês
  permanecem no idioma original, e o agrupamento entre idiomas não é garantido.
- O destaque considera o número de veículos e a diversidade de regiões no grupo,
  não uma avaliação editorial da importância da notícia.
- Um feed pode representar uma editoria específica, e a ordem do feed não equivale
  necessariamente à capa editorial do veículo.
- Exige pelo menos 20 das 40 fontes com notícias recentes antes de substituir a edição.
- Preserva a edição anterior se a geração falhar. O rodapé informa a data da última edição
  e as fontes sem notícias recentes, sem preencher lacunas com títulos fictícios.
- Todas as histórias agrupadas são exibidas; não há corte silencioso dos grupos excedentes.

## Fontes

`outlets.csv` contém a seleção ativa, com os feeds configurados.
`outlets-completo.csv` preserva o cadastro original de 408 veículos para consultas futuras.
A lista ativa não depende de todas as fontes publicarem todos os dias.

Na validação inicial, alguns feeds da proposta anterior estavam bloqueados, vazios ou antigos.
Foram substituídos: Estadão → Manual do Usuário; CNN → Drop Site News;
New York Times → The New Yorker; Politico → Platformer; USA Today → Defector;
Los Angeles Times → The Marshall Project; The Intercept → CNBC.
O Globo, Valor e Nexo tiveram seus endereços de feed corrigidos.
A disponibilidade pode mudar, inclusive entre o ambiente local e os executores do GitHub.

## Executar localmente

Requer Python 3.12.

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python capa.py
python -m http.server 8000 --directory docs
```

Abra http://localhost:8000. Para uma prévia explicitamente fictícia, sem rede:

```sh
python capa.py --demo --saida /tmp/capa-demo
```

Para verificar os testes:

```sh
python -m pip install pytest==9.1.1
python -m pytest tests -q
```

## Diagnóstico e manutenção

- `data/ultima_execucao.json`: resultado da última edição gerada.
- `data/ultima_tentativa.json`: cobertura da tentativa, inclusive quando insuficiente.
- `data/feeds_cache.json`: descoberta e cache dos feeds.
- Em **Actions**, o artefato `diagnostico` é guardado por sete dias, inclusive após falhas.
- O workflow faz commit das edições e do cache; a publicação ocorre explicitamente com
  `upload-pages-artifact` e `deploy-pages`, sem depender do commit do robô para disparar Pages.
- Para trocar uma fonte, edite `outlets.csv`, preservando 20 entradas de cada país, e execute novamente.
- O acesso à página é público. Não coloque senhas, chaves ou informação privada no repositório.
