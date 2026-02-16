#!/usr/bin/env python3
"""Automated AI PR review (pluggable agent)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ----------------------------
# Git helpers
# ----------------------------

def run(cmd: List[str], cwd: Path | None = None, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd, check=check)


def ensure_repo_root() -> Path:
    try:
        cp = git("rev-parse", "--show-toplevel")
        return Path(cp.stdout.strip())
    except subprocess.CalledProcessError as e:
        print("ERROR: Not inside a git repository.", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        raise SystemExit(2)


def _try_repo_root() -> Path | None:
    """Return repo root if inside a git repo, else None. Does not exit."""
    try:
        cp = git("rev-parse", "--show-toplevel", check=True)
        return Path(cp.stdout.strip())
    except subprocess.CalledProcessError:
        return None


def _default_config_paths(repo: Path | None) -> List[Path]:
    """Return paths to check for config, in search order."""
    cwd = Path.cwd()
    paths: List[Path] = []
    # 1. Current directory
    paths.append(cwd / ".coderev.json")
    paths.append(cwd / "coderev.json")
    # 2. Repo root (if different from cwd)
    if repo and repo != cwd:
        paths.append(repo / ".coderev.json")
        paths.append(repo / "coderev.json")
    # 3. User config
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        paths.append(Path(appdata) / "coderev" / "config.json")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        paths.append(Path(xdg) / "coderev" / "config.json")
    return paths


def load_config(config_path: str | None, no_config: bool, repo: Path | None) -> Dict:
    """Load config from file. Returns dict of key -> value. Empty dict if disabled/not found."""
    if no_config:
        return {}
    if config_path:
        p = Path(config_path)
        if not p.is_absolute():
            p = (repo or Path.cwd()) / p
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError) as e:
                print(f"WARNING: Could not load config from {p}: {e}", file=sys.stderr)
        return {}
    for p in _default_config_paths(repo):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError) as e:
                print(f"WARNING: Could not load config from {p}: {e}", file=sys.stderr)
            break
    return {}


def fetch_base(base_ref: str, repo: Path) -> None:
    try:
        if "/" in base_ref and base_ref.startswith(("origin/", "upstream/")):
            remote = base_ref.split("/", 1)[0]
            git("fetch", "--prune", remote, cwd=repo)
        else:
            git("fetch", "--prune", "origin", cwd=repo)
    except subprocess.CalledProcessError:
        pass


def checkout_branch(branch: str, repo: Path) -> None:
    try:
        git("rev-parse", "--verify", branch, cwd=repo)
        git("checkout", branch, cwd=repo)
        return
    except subprocess.CalledProcessError:
        pass

    try:
        git("checkout", "-B", branch, f"origin/{branch}", cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Could not check out branch '{branch}'.", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        raise SystemExit(2)


def compute_diff(base_ref: str, head_ref: str, repo: Path) -> str:
    try:
        cp = git("diff", "--no-color", f"{base_ref}...{head_ref}", cwd=repo)
        return cp.stdout
    except subprocess.CalledProcessError as e:
        print("ERROR: Failed to compute git diff.", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        raise SystemExit(2)


def list_changed_files(base_ref: str, head_ref: str, repo: Path) -> List[str]:
    cp = git("diff", "--name-only", f"{base_ref}...{head_ref}", cwd=repo)
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


# ----------------------------
# Diff context extraction
# ----------------------------

@dataclass(frozen=True)
class ChangedHunk:
    file_path: str
    start_new: int
    count_new: int
    start_old: int
    count_old: int


HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_changed_hunks(diff_text: str) -> List[ChangedHunk]:
    hunks: List[ChangedHunk] = []
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            continue
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/") :].strip()
            continue
        m = HUNK_RE.search(line)
        if m and current_file:
            start_old = int(m.group(1))
            count_old = int(m.group(2) or "1")
            start_new = int(m.group(3))
            count_new = int(m.group(4) or "1")
            hunks.append(
                ChangedHunk(
                    file_path=current_file,
                    start_new=start_new,
                    count_new=count_new,
                    start_old=start_old,
                    count_old=count_old,
                )
            )
    return hunks


def get_file_content(path: Path, max_bytes: int) -> str:
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if len(data) > max_bytes:
        head = data[: max_bytes // 2]
        tail = data[-(max_bytes // 2) :]
        return (head + b"\n\n... (truncated) ...\n\n" + tail).decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


def build_context_snippets(
    repo: Path,
    hunks: List[ChangedHunk],
    context_lines: int,
    per_file_max_chars: int,
) -> Dict[str, str]:
    grouped: Dict[str, List[ChangedHunk]] = {}
    for h in hunks:
        grouped.setdefault(h.file_path, []).append(h)

    snippets: Dict[str, str] = {}
    for rel_path, fhunks in grouped.items():
        abs_path = repo / rel_path
        if not abs_path.exists() or abs_path.is_dir():
            continue

        content = abs_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        out_parts: List[str] = []

        for h in fhunks:
            start = max(1, h.start_new - context_lines)
            end = min(len(lines), h.start_new + max(h.count_new, 1) + context_lines - 1)

            block = []
            for i in range(start, end + 1):
                is_changed_range = h.start_new <= i < (h.start_new + max(h.count_new, 1))
                prefix = ">>" if is_changed_range else "  "
                block.append(f"{prefix} {i:6d} | {lines[i-1]}")
            out_parts.append(
                f"--- Context around hunk (+{h.start_new},{h.count_new}) in {rel_path} ---\n"
                + "\n".join(block)
            )

        combined = "\n\n".join(out_parts).strip()
        if len(combined) > per_file_max_chars:
            combined = combined[: per_file_max_chars] + "\n... (snippet truncated) ..."
        snippets[rel_path] = combined

    return snippets


def read_docs(paths: List[Path], repo: Path, max_bytes: int) -> List[Tuple[str, str]]:
    docs: List[Tuple[str, str]] = []
    for p in paths:
        abs_p = p if p.is_absolute() else (repo / p)
        if not abs_p.exists():
            print(f"WARNING: Doc not found, skipping: {p}", file=sys.stderr)
            continue
        docs.append((str(p), get_file_content(abs_p, max_bytes=max_bytes)))
    return docs


# ----------------------------
# Prompt builder
# ----------------------------

def build_prompt(
    branch: str,
    base_ref: str,
    changed_files: List[str],
    diff_text: str,
    obey_docs: List[Tuple[str, str]],
    result_template: Tuple[str, str] | None,
    file_snippets: Dict[str, str],
    include_full_files: bool,
    repo: Path,
    max_file_bytes: int,
) -> str:
    parts: List[str] = []
    parts.append("# Task: Automated PR Review\n")
    parts.append(f"Branch under review: {branch}\n")
    parts.append(f"Diff base: {base_ref}\n")

    parts.append("## Instructions\n")
    parts.append(
        "Perform a PR review. Obey the provided documentation. "
        "Focus on correctness, maintainability, security, performance, and tests.\n"
    )

    if obey_docs:
        parts.append("\n## Documentation to OBEY (highest priority)\n")
        for name, content in obey_docs:
            parts.append(f"\n### {name}\n{content}")

    if result_template:
        tname, tcontent = result_template
        parts.append("\n## Result Template (fill this in)\n")
        parts.append(f"\n### {tname}\n{tcontent}")

    parts.append("\n## Changed Files\n" + "\n".join(f"- {f}" for f in changed_files))

    parts.append("\n\n## Diff\n")
    parts.append(diff_text if diff_text.strip() else "(No diff)")

    if file_snippets:
        parts.append("\n\n## Extra Context (snippets around changes)\n")
        for f, snip in file_snippets.items():
            if snip.strip():
                parts.append(f"\n### {f}\n{snip}")

    if include_full_files and changed_files:
        parts.append("\n\n## Full Contents of Changed Files\n")
        for rel in changed_files:
            abs_path = repo / rel
            if not abs_path.exists() or abs_path.is_dir():
                continue
            parts.append(f"\n### {rel}\n{get_file_content(abs_path, max_bytes=max_file_bytes)}")

    parts.append(
        "\n\n## Output Requirements\n"
        "- If a result template was provided, fill it in.\n"
        "- Otherwise, output markdown with: Summary, Major Issues, Minor Issues, Tests, Suggestions.\n"
        "- Be specific (file paths and line ranges when possible).\n"
    )

    return "\n".join(parts).strip() + "\n"


# ----------------------------
# Pluggable agent runner
# ----------------------------

@dataclass(frozen=True)
class AgentSpec:
    """
    cmd: command (string) or list. If string, it's parsed with shlex.
    mode:
      - stdin: prompt goes to stdin
      - arg: prompt substituted into one argument via {prompt}
      - file: prompt written to temp file, file path substituted via {prompt_file}
    """
    name: str
    cmd: List[str]
    mode: str  # stdin | arg | file
    cwd: str = "."  # relative to repo root
    env: Dict[str, str] | None = None


def which_or_die(exe: str) -> None:
    if shutil.which(exe) is None:
        print(f"ERROR: Command not found on PATH: {exe}", file=sys.stderr)
        raise SystemExit(2)


def load_agent_spec(agent: str, agent_config: str | None) -> AgentSpec:
    # Built-in presets (override with --agent-config).
    presets: Dict[str, AgentSpec] = {
        "codex": AgentSpec(
            name="codex",
            cmd=["codex", "exec", "-"],
            mode="stdin"
        ),
        "copilot": AgentSpec(
            name="copilot",
            cmd=["copilot", "--prompt-file", "{prompt_file}"],
            mode="file",
        ),
    }

    if agent_config:
        raw = json.loads(agent_config)
        cmd = raw["cmd"]
        cmd_list = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        return AgentSpec(
            name=raw.get("name", agent),
            cmd=cmd_list,
            mode=raw.get("mode", "stdin"),
            cwd=raw.get("cwd", "."),
            env=raw.get("env"),
        )

    if agent not in presets:
        print(f"ERROR: Unknown agent '{agent}'. Use --agent codex|copilot or provide --agent-config JSON.", file=sys.stderr)
        raise SystemExit(2)

    return presets[agent]


def run_agent(agent: AgentSpec, prompt: str, repo: Path) -> str:
    if not agent.cmd:
        print("ERROR: Agent cmd is empty.", file=sys.stderr)
        raise SystemExit(2)

    cwd = repo / agent.cwd
    env = os.environ.copy()
    if agent.env:
        env.update({k: str(v) for k, v in agent.env.items()})

    cmd = list(agent.cmd)
    exe = cmd[0]

    resolved_exe = shutil.which(exe)
    if not resolved_exe:
        print(f"ERROR: Command not found on PATH: {exe}", file=sys.stderr)
        raise SystemExit(2)

    cmd[0] = resolved_exe

    if os.name == "nt":
        suffix = Path(resolved_exe).suffix.lower()

        if suffix in {".cmd", ".bat"}:
            comspec = env.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
            if not Path(comspec).exists():
                print(f"ERROR: ComSpec points to missing cmd.exe: {comspec}", file=sys.stderr)
                raise SystemExit(2)
            cmd = [comspec, "/d", "/s", "/c", *cmd]

        elif suffix == ".ps1":
            ps = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            if not Path(ps).exists():
                print(f"ERROR: powershell.exe not found (resolved: {ps})", file=sys.stderr)
                raise SystemExit(2)
            cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *cmd]

    try:
        if agent.mode == "stdin":
            cp = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        elif agent.mode == "arg":
            cmd = [a.replace("{prompt}", prompt) for a in cmd]
            cp = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        elif agent.mode == "file":
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md") as f:
                f.write(prompt)
                tmp_path = f.name
            try:
                cmd = [a.replace("{prompt_file}", tmp_path) for a in cmd]
                cp = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        else:
            print(f"ERROR: Unknown agent mode '{agent.mode}'. Expected stdin|arg|file.", file=sys.stderr)
            raise SystemExit(2)
    except FileNotFoundError as e:
        print("ERROR: Failed to start agent process.", file=sys.stderr)
        print(f"Command: {cmd}", file=sys.stderr)
        raise

    if cp.returncode != 0:
        print("ERROR: Agent CLI returned non-zero exit code.", file=sys.stderr)
        if cp.stderr.strip():
            print(cp.stderr, file=sys.stderr)
        raise SystemExit(cp.returncode)

    return cp.stdout


# ----------------------------
# Main
# ----------------------------

def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", "-c", metavar="PATH", help="Path to config file (JSON).")
    pre.add_argument("--no-config", action="store_true", help="Do not load any config file.")
    pre_args, _ = pre.parse_known_args()

    repo_for_config = _try_repo_root()
    cfg = load_config(pre_args.config, pre_args.no_config, repo_for_config)

    def _default(key: str, env_var: str | None, fallback: str | int | bool):
        if env_var and env_var in os.environ:
            v = os.environ[env_var]
            if isinstance(fallback, bool):
                return v.lower() in ("1", "true", "yes")
            if isinstance(fallback, int):
                return int(v)
            return v
        if key in cfg:
            return cfg[key]
        return fallback

    def _default_int(key: str, env_var: str, fallback: int) -> int:
        v = _default(key, env_var, fallback)
        return int(v) if not isinstance(v, int) else v

    ap = argparse.ArgumentParser(description="Automated AI PR review (pluggable agent).")
    ap.add_argument("--config", "-c", metavar="PATH", help="Path to config file (JSON).")
    ap.add_argument("--no-config", action="store_true", help="Do not load any config file.")
    ap.add_argument("branch", help="Branch name to review (will be checked out).")

    ap.add_argument(
        "--base-ref",
        default=_default("base-ref", "CODEREV_BASE_REF", "origin/main"),
    )
    ap.add_argument(
        "--head-ref",
        default=_default("head-ref", None, "HEAD"),
    )

    obey_doc_default = cfg.get("obey-doc", [])
    if isinstance(obey_doc_default, str):
        obey_doc_default = [obey_doc_default]
    ap.add_argument(
        "--obey-doc",
        action="append",
        default=[],
        help="Repeatable. Files the agent must obey.",
    )
    ap.add_argument(
        "--template",
        default=_default("template", "CODEREV_TEMPLATE", ""),
    )

    ap.add_argument(
        "--agent",
        default=_default("agent", "CODEREV_AGENT", "codex"),
        help="codex|copilot (or use --agent-config).",
    )
    agent_cfg_default = _default("agent-config", "CODEREV_AGENT_CONFIG", "")
    if isinstance(agent_cfg_default, dict):
        agent_cfg_default = json.dumps(agent_cfg_default)
    ap.add_argument(
        "--agent-config",
        default=agent_cfg_default,
        help=(
            "JSON to define the agent. Example: "
            '\'{"name":"codex","mode":"stdin","cmd":["codex","exec","-"]}\' '
            'or \'{"name":"mycli","mode":"file","cmd":"mycli review --in {prompt_file}"}\''
        ),
    )

    ap.add_argument(
        "--context-lines",
        type=int,
        default=_default_int("context-lines", "CODEREV_CONTEXT_LINES", 20),
    )
    ap.add_argument(
        "--include-full-files",
        action="store_true",
        default=_default("include-full-files", None, False),
    )

    ap.add_argument(
        "--max-diff-bytes",
        type=int,
        default=_default_int("max-diff-bytes", "CODEREV_MAX_DIFF_BYTES", 600000),
    )
    ap.add_argument(
        "--max-doc-bytes",
        type=int,
        default=_default_int("max-doc-bytes", "CODEREV_MAX_DOC_BYTES", 200000),
    )
    ap.add_argument(
        "--max-file-bytes",
        type=int,
        default=_default_int("max-file-bytes", "CODEREV_MAX_FILE_BYTES", 200000),
    )
    ap.add_argument(
        "--snippet-max-chars",
        type=int,
        default=_default_int("snippet-max-chars", "CODEREV_SNIPPET_MAX_CHARS", 25000),
    )

    ap.add_argument(
        "--out",
        default=_default("out", "CODEREV_OUT", ""),
        help="Write agent output to this file.",
    )

    args = ap.parse_args()

    obey_doc_from_cfg = cfg.get("obey-doc", [])
    if isinstance(obey_doc_from_cfg, str):
        obey_doc_from_cfg = [obey_doc_from_cfg]
    args.obey_doc = list(obey_doc_from_cfg) + list(args.obey_doc)

    repo = ensure_repo_root()
    fetch_base(args.base_ref, repo)
    checkout_branch(args.branch, repo)

    diff_text = compute_diff(args.base_ref, args.head_ref, repo)
    diff_bytes = diff_text.encode("utf-8", errors="replace")
    if len(diff_bytes) > args.max_diff_bytes:
        diff_text = diff_bytes[: args.max_diff_bytes].decode("utf-8", errors="replace") + "\n... (diff truncated) ...\n"

    changed_files = list_changed_files(args.base_ref, args.head_ref, repo)

    hunks = parse_changed_hunks(diff_text)
    snippets = build_context_snippets(repo, hunks, args.context_lines, args.snippet_max_chars)

    obey_docs = read_docs([Path(p) for p in args.obey_doc], repo=repo, max_bytes=args.max_doc_bytes)

    template_tuple: Tuple[str, str] | None = None
    if args.template:
        tp = Path(args.template)
        t_abs = tp if tp.is_absolute() else (repo / tp)
        if t_abs.exists():
            template_tuple = (args.template, get_file_content(t_abs, max_bytes=args.max_doc_bytes))
        else:
            print(f"WARNING: Template not found, skipping: {args.template}", file=sys.stderr)

    prompt = build_prompt(
        branch=args.branch,
        base_ref=args.base_ref,
        changed_files=changed_files,
        diff_text=diff_text,
        obey_docs=obey_docs,
        result_template=template_tuple,
        file_snippets=snippets,
        include_full_files=args.include_full_files,
        repo=repo,
        max_file_bytes=args.max_file_bytes,
    )
    prompt = prompt.replace("\u2011", "-")

    agent_spec = load_agent_spec(args.agent, args.agent_config or None)
    output = run_agent(agent_spec, prompt, repo)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = repo / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote review output to: {out_path}", file=sys.stderr)

    print(output)
    return 0
