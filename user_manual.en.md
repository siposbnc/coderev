# coderev – User Manual

AI-powered PR (Pull Request) review command-line tool. It analyzes the changes of the specified branch based on diff, passes them to a configurable AI agent (e.g. Codex, Copilot), and outputs the review.

**Requirement:** Must be run from a Git repository root directory.

---

## Installation

### Global command (runnable from anywhere)

To make the `coderev` command available from anywhere:

```bash
pip install .
```

This installs into the system Python; the `Scripts` directory must be on your PATH (this is usually the default).

With Poetry, for global installation (without venv):

```bash
poetry config virtualenvs.create false
poetry install
```

### Poetry venv (project-specific)

```bash
poetry install
poetry run coderev <branch>    # every time
# or: poetry shell             # then coderev anytime in the shell
```

**Note:** A plain `poetry install` installs into a virtual environment – from there `coderev` is only available via `poetry run` or inside an activated `poetry shell`, not elsewhere.

Alternative, run without installation (from the project directory):

```bash
python -m coderev <branch> [options]
```

In development mode (code changes take effect immediately, newer pip required):

```bash
pip install -e .
```

---

## Configuration file

Options that are not environment variables (e.g. `--obey-doc`, `--include-full-files`) can be specified in the configuration file, so you don't have to provide them on every run.

**Search order:**
1. The specified file (`--config` / `-c`)
2. `.coderev.json` or `coderev.json` in the current directory
3. The same in the repository root
4. User config: `%APPDATA%\coderev\config.json` (Windows) or `~/.config/coderev/config.json` (Linux/macOS)

**Precedence:** command-line argument > environment variable > configuration file > built-in default.

**Example config (JSON):**

```json
{
  "obey-doc": ["CONTRIBUTING.md", "docs/style.md"],
  "template": "review_template.md",
  "include-full-files": false,
  "base-ref": "origin/main",
  "agent": "codex",
  "context-lines": 20,
  "out": "review.md"
}
```

| Config key | Type | Description |
|------------|------|-------------|
| `obey-doc` | string or array | Path(s) to document(s) – values from the command line are appended to this |
| `template` | string | Path to template file |
| `include-full-files` | boolean | Whether full files should be included in the prompt |
| `base-ref` | string | Base reference for diff |
| `head-ref` | string | Head reference for diff |
| `agent` | string | codex \| copilot |
| `agent-config` | object or JSON string | Custom agent configuration |
| `context-lines` | number | Number of context lines |
| `max-diff-bytes`, `max-doc-bytes`, `max-file-bytes`, `snippet-max-chars` | number | Size limits |
| `out` | string | Output file path |

Config loading can be disabled with the `--no-config` flag.

---

## Usage

```bash
coderev <branch> [options]
```

Or without installation (from the project directory):

```bash
python -m coderev <branch> [options]
python coderev.py <branch> [options]   # backwards compatible
```

---

## Arguments

### Required positional

| Argument | Description |
|----------|-------------|
| `branch` | Name of the branch to review. The script checkouts this branch, then computes the diff against the base. |

---

### Configuration

| Argument | Description |
|----------|-------------|
| `--config`, `-c` | Path to configuration file (JSON). If not specified, automatic search (see above). |
| `--no-config` | Do not load the configuration file. |

---

### Diff reference

| Argument | Default | Environment variable | Description |
|----------|---------|----------------------|-------------|
| `--base-ref` | `origin/main` | `coderev_BASE_REF` | Base reference for comparison. If prefixed with `origin/` or `upstream/`, the corresponding remote will be fetched first. |
| `--head-ref` | `HEAD` | — | Head reference for the diff. Usually the branch's latest commit. |

---

### Documentation and template

| Argument | Default | Environment variable | Description |
|----------|---------|----------------------|-------------|
| `--obey-doc` | *(empty)* | — | Document(s) to include in the prompt and enforce compliance. Can be specified multiple times. File path relative to the repo (or absolute). |
| `--template` | *(empty)* | `coderev_TEMPLATE` | Path to template file. If specified, the AI returns the result in the format defined by this template. |

---

### AI agent

| Argument | Default | Environment variable | Description |
|----------|---------|----------------------|-------------|
| `--agent` | `codex` | `coderev_AGENT` | Built-in agent: `codex` (Codex exec, stdin) or `copilot` (expects prompt file). |
| `--agent-config` | *(empty)* | `coderev_AGENT_CONFIG` | Custom agent JSON configuration. Overrides the `--agent` value. Example: `'{"name":"codex","mode":"stdin","cmd":["codex","exec","-"]}'` – `mode`: `stdin` / `arg` / `file`; use `{prompt}` or `{prompt_file}` placeholders in `cmd`. |

---

### Context and size limits

| Argument | Default | Environment variable | Description |
|----------|---------|----------------------|-------------|
| `--context-lines` | `20` | `coderev_CONTEXT_LINES` | Number of context lines around diff hunks. These are included in the prompt. |
| `--include-full-files` | *(off)* | — | If enabled, the full content of changed files is included in the prompt (up to the `--max-file-bytes` limit). |
| `--max-diff-bytes` | `600000` | `coderev_MAX_DIFF_BYTES` | Maximum diff size in bytes. Content above the limit is truncated. |
| `--max-doc-bytes` | `200000` | `coderev_MAX_DOC_BYTES` | Maximum size of an `--obey-doc` or `--template` file in bytes. |
| `--max-file-bytes` | `200000` | `coderev_MAX_FILE_BYTES` | Maximum size of a file in bytes when `--include-full-files` is used. |
| `--snippet-max-chars` | `25000` | `coderev_SNIPPET_MAX_CHARS` | Maximum length of a file's context snippet in characters. |

---

### Output

| Argument | Default | Environment variable | Description |
|----------|---------|----------------------|-------------|
| `--out` | *(empty)* | `coderev_OUT` | File path. If specified, the AI's response is saved here (relative to the repo root or absolute path). The directory is created if needed for relative paths. |

---

## Examples

```bash
# Simple run: review feature/xyz branch against origin/main
coderev feature/xyz

# Against develop branch, with Codex
coderev feature/xyz --base-ref origin/develop --agent codex

# With document and template, result to file
coderev feature/xyz --obey-doc CONTRIBUTING.md --template review_template.md --out review.md

# With full files and multiple documents
coderev feature/xyz --obey-doc docs/style.md --obey-doc docs/api.md --include-full-files
```
