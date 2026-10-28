IZI CONTAS - PROJETO ADMINISTRATIVO-FINANCEIRO (N2)
Extração de dados de nota fiscal (PDF) com IA (Gemini)

- Link Hospedagem:
  <<PREENCHER: link do Render, ex.: https://izicontas.onrender.com>>

- GitHub:
  https://github.com/VilelaGuilherme/IziContas

- Login:
  A aplicação não possui tela de login (acesso livre pelo link).

- Senha:
  Não se aplica.

- Key (LLM) - chave da API do Gemini:
  <<PREENCHER: cole a chave só na cópia que for para o Classroom>>
  Como usar: colar no campo "Chave da API do Gemini", no topo da tela principal.

- Código da última atualização no GitHub:
  Aparece na tela principal, logo abaixo do título ("Última atualização no GitHub"),
  com o selo informando se a versão que está no ar é a mais recente.
  O histórico dos últimos commits fica no card "Versionamento (GitHub)", no fim da mesma tela.
  (O código muda a cada commit, por isso não fica escrito neste arquivo.)

COMO TESTAR
1. Abrir o link da hospedagem.
   Obs.: no plano gratuito o serviço "dorme" quando fica parado; o primeiro acesso
   pode levar cerca de 1 minuto para carregar.
2. Colar a Key (LLM) no campo da chave.
3. Escolher o PDF da nota fiscal e clicar em "EXTRAIR DADOS".
4. Os dados aparecem em JSON (aba "JSON") e em tabela (aba "Visualização Formatada").
5. Logo abaixo aparece a análise no banco de dados: FORNECEDOR, FATURADO e DESPESA com
   "EXISTE - ID" ou "NÃO EXISTE".
6. Clicar em "LANÇAR REGISTRO" cria o que faltar e grava o movimento A PAGAR com a parcela.
7. A tela "Cadastros" (menu no topo) lista Pessoas, Classificações e Contas a pagar, com busca,
   edição, exclusão lógica (INATIVO) e reativação.
