# IZI Contas — Agent1 (Extração de Nota Fiscal via Gemini)

Implementação da 1ª etapa da atividade: um Agent que recebe uma nota fiscal
em PDF, extrai os dados estruturados e classifica automaticamente o tipo de
despesa, devolvendo tudo em JSON.

## Estrutura

```
izi_contas_agent/
├── agents/
│   └── agent1/
│       ├── __init__.py            # exporta a classe Agent1
│       ├── manipulacao_dados.py   # Agent1: schema do JSON, categorias de despesa, prompt e chamada ao Gemini
│       └── consulta_dados.py      # reservado para próxima etapa
├── templates/
│   └── index.html                 # interface web (upload + botão + abas Formatada/JSON)
├── main.py                        # servidor Flask
└── requirements.txt
```

## Passo a passo

### 1. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 2. Obter uma chave de API do Gemini

1. Acesse https://aistudio.google.com/app/apikey
2. Crie uma API key gratuita (não precisa de cartão de crédito no free tier)

### 3. Chave da API

A chave pode ser **informada direto na tela** (campo "Chave da API do Gemini"): ela
é enviada só na hora da extração, não é gravada nem aparece em erros. Se preferir
uma chave fixa no servidor, defina a variável `GEMINI_API_KEY` (a chave da tela,
quando preenchida, tem prioridade):

```bash
# Linux / macOS
export GEMINI_API_KEY="sua-chave-aqui"

# Windows (PowerShell)
$env:GEMINI_API_KEY="sua-chave-aqui"
```

### 4. Rodar o servidor

```bash
python main.py
```

Acesse http://localhost:5000 no navegador, escolha o PDF da nota fiscal e clique
em "Extrair Dados". Os dados aparecem na aba "JSON" (com botões Copiar e Baixar JSON)
e na aba "Visualização Formatada".

## Testando sem interface (linha de comando)

```python
from agents.agent1 import Agent1

agent1 = Agent1()
dados = agent1.extrair_dados("caminho/para/nota.pdf")
print(dados)
```

## Sobre a classificação de despesa

O campo `tipo_despesa` não existe na nota fiscal — é inferido pelo Gemini a
partir da descrição dos produtos (ex: "Óleo Diesel" → MANUTENÇÃO E OPERAÇÃO).
O modelo só pode escolher entre as categorias do enunciado, que ficam no
dicionário `CATEGORIAS_DESPESA` em `agents/agent1/manipulacao_dados.py`. Para
acrescentar uma nova despesa, basta incluir uma linha ali (prompt e schema se
atualizam sozinhos).

O JSON é gerado pelo Gemini já no formato do schema `NotaFiscal` (saída
estruturada), e não como texto livre.

## Formato do JSON retornado

```json
{
  "fornecedor": {
    "razao_social": "string ou null",
    "nome_fantasia": "string ou null",
    "cnpj": "string ou null"
  },
  "faturado": {
    "nome_completo": "string ou null",
    "cpf": "string ou null"
  },
  "numero_nota_fiscal": "string ou null",
  "data_emissao": "DD/MM/AAAA ou null",
  "descricao_produtos": ["string", "..."],
  "quantidade_parcelas": 1,
  "parcelas": [
    {"numero": 1, "valor": 0.0, "data_vencimento": "DD/MM/AAAA ou null"}
  ],
  "valor_total": 0.0,
  "tipo_despesa": ["MANUTENÇÃO E OPERAÇÃO"]
}
```

`quantidade_parcelas` é calculado pelo código (`len(parcelas)`).
`parcelas` e `tipo_despesa` são sempre listas — hoje só vêm com um item cada
(como pede a 1ª etapa), mas a estrutura já comporta mais de um, sem precisar
mudar o formato depois.

## Hospedagem (exemplo: Render)

1. Suba o projeto para o GitHub (veja abaixo).
2. No Render: **New > Web Service** e conecte o repositório.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn main:app --timeout 120` (o `Procfile` já traz o mesmo comando)
5. Em **Environment**, crie:
   - `GITHUB_REPO` = `seu-usuario/seu-repositorio` (usado pelo card de versionamento)
   - `GEMINI_API_KEY` (opcional: sem ela, cada usuário informa a chave na tela)
   - `GITHUB_TOKEN` (opcional: só para repositório privado ou se o limite da API do GitHub estourar)
6. Coloque o link público da aplicação no relatório/entrega.

## GitHub

```bash
git init
git add .
git commit -m "Primeira versão: extração de nota fiscal com Gemini"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git push -u origin main
```

Use commits pequenos e com mensagens claras, pois a própria aplicação lista os 10
últimos commits (card **Versionamento (GitHub)**, rota `/versao`) para demonstrar o
histórico sem precisar da apresentação presencial. O `.gitignore` já impede que
`.env`, `uploads/` e `__pycache__/` vão para o repositório. **Nunca** faça commit da
chave da API.

## Modelo e erro 503 (sobrecarga)

O modelo principal é `gemini-3.8-flash` (pode ser trocado pela variável `GEMINI_MODEL`).
Se ele responder 503 (alta demanda), o código tenta de novo e depois passa
automaticamente para `gemini-3.5-flash-lite`. Erros de chave ou cota não trocam de
modelo. A lista de reserva fica em `MODELOS_RESERVA`, em `agents/agent1/manipulacao_dados.py`.
