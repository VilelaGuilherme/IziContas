# main.py
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

from flask import Flask, render_template, request, jsonify

from agents.agent1 import Agent1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.json.sort_keys = False  # mantém a ordem dos campos do JSON (Flask ordena por padrão)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Repositório no formato "usuario/repositorio" (definir na hospedagem)
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
_cache_commits = {"quando": 0, "dados": None}


# ---------------------------------------------------------------- chave
def obter_chave() -> str:
    """Chave enviada pelo usuário na tela (tem prioridade) ou, se não vier,
    a GEMINI_API_KEY configurada no servidor. Nunca é gravada nem logada."""
    return (request.headers.get("X-Gemini-Key", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip())


# ---------------------------------------------------------------- versionamento
def _commit_em_execucao() -> str:
    """Hash do commit que está rodando (git local ou variável da hospedagem)."""
    for var in ("RENDER_GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA", "SOURCE_VERSION",
                "HEROKU_SLUG_COMMIT", "GIT_COMMIT"):
        if os.environ.get(var):
            return os.environ[var][:7]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR,
            stderr=subprocess.DEVNULL, timeout=3)
        return out.decode().strip()
    except Exception:
        return None


def _buscar_commits_github(repo: str, limite: int = 10) -> list:
    url = f"https://api.github.com/repos/{repo}/commits?per_page={limite}"
    headers = {"User-Agent": "izi-contas", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):  # opcional (repositório privado / limite de uso)
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
        itens = json.load(r)
    return [{
        "sha": c["sha"][:7],
        "mensagem": c["commit"]["message"].split("\n")[0],
        "autor": c["commit"]["author"]["name"],
        "data": c["commit"]["author"]["date"],
        "url": c["html_url"],
    } for c in itens]


@app.route("/versao")
def versao():
    resp = {
        "repositorio": GITHUB_REPO or None,
        "url": f"https://github.com/{GITHUB_REPO}" if GITHUB_REPO else None,
        "commit_em_execucao": _commit_em_execucao(),
        "chave_no_servidor": bool(os.environ.get("GEMINI_API_KEY")),
        "commits": [],
        "erro": None,
    }
    if not GITHUB_REPO:
        resp["erro"] = "Variável GITHUB_REPO não configurada (ex.: usuario/repositorio)."
        return jsonify(resp)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", GITHUB_REPO):
        resp["erro"] = "GITHUB_REPO inválido. Use o formato usuario/repositorio."
        return jsonify(resp)

    # cache de 60s para não estourar o limite da API do GitHub
    if _cache_commits["dados"] is not None and time.time() - _cache_commits["quando"] < 60:
        resp["commits"] = _cache_commits["dados"]
        return jsonify(resp)
    try:
        commits = _buscar_commits_github(GITHUB_REPO)
        _cache_commits.update(quando=time.time(), dados=commits)
        resp["commits"] = commits
    except Exception as exc:
        resp["erro"] = f"Não foi possível consultar o GitHub: {exc}"
    return jsonify(resp)


# ---------------------------------------------------------------- rotas
@app.errorhandler(413)
def arquivo_grande(_):
    return jsonify({"erro": "Arquivo muito grande (máximo 10 MB)."}), 413


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/extrair", methods=["POST"])
def extrair():
    chave = obter_chave()
    if not chave:
        return jsonify({"erro": "Informe a chave da API do Gemini no campo da tela."}), 400

    file = request.files.get("nota_fiscal")
    if not file or file.filename == "":
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"erro": "O arquivo precisa ser um PDF."}), 400

    # nome aleatório: evita conflito entre uploads e nomes maliciosos
    file_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    file.save(file_path)

    try:
        dados = Agent1(api_key=chave).extrair_dados(file_path)
    except Exception as exc:
        # a chave nunca deve aparecer na resposta
        return jsonify({"erro": str(exc).replace(chave, "***")}), 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    return jsonify(dados)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
