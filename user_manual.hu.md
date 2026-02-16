# coderev – Felhasználói kézikönyv

AI-alapú PR (Pull Request) ellenőrző parancssori eszköz. A megadott ág módosításait diff alapján elemzi, egy konfigurálható AI-ügynöknek (pl. Codex, Copilot) adja át, és a véleményt kiírja.

**Feltétel:** Git repó gyökérkönyvtárában kell futtatni.

---

## Telepítés

### Globális parancs (bárhonnan futtatható)

A `coderev` parancs bárhonnan elérhető legyen:

```bash
pip install .
```

Ez a rendszer Pythonba telepít; a `Scripts` könyvtárnak a PATH-on kell lennie (általában ez alapból így van).

Poetry-vel globális telepítéshez (venv nélkül):

```bash
poetry config virtualenvs.create false
poetry install
```

### Poetry venv (projekt-specifikus)

```bash
poetry install
poetry run coderev <branch>    # minden alkalommal
# vagy: poetry shell            # majd coderev bármikor a shellben
```

**Megjegyzés:** A sima `poetry install` egy virtuális környezetbe telepít – onnan a `coderev` csak `poetry run`-nal vagy egy aktivált `poetry shell`-ben érhető el, máshol nem.

Alternatíva, telepítés nélkül (a projekt könyvtárából):

```bash
python -m coderev <branch> [opciók]
```

Fejlesztői módban (a kód módosításai azonnal érvényesek, újabb pip szükséges):

```bash
pip install -e .
```

---

## Konfigurációs fájl

A nem környezeti változóval rendelkező opciók (pl. `--obey-doc`, `--include-full-files`) a konfigurációs fájlban is megadhatók, így nem kell minden futtatáskor megadni őket.

**Keresési sorrend:**
1. A megadott fájl (`--config` / `-c`)
2. `.coderev.json` vagy `coderev.json` az aktuális könyvtárban
3. Ugyanezek a repó gyökerében
4. Felhasználói config: `%APPDATA%\coderev\config.json` (Windows) vagy `~/.config/coderev/config.json` (Linux/macOS)

**Precedencia:** parancssori argumentum > környezeti változó > konfigurációs fájl > beépített alapérték.

**Példa config (JSON):**

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

| Config kulcs | Típus | Leírás |
|--------------|-------|--------|
| `obey-doc` | string vagy tömb | Dokumentum(ok) elérési útja – a parancssorból megadott értékek ehhez csatlakoznak |
| `template` | string | Sablonfájl elérési útja |
| `include-full-files` | boolean | Teljes fájlok belekerüljenek-e a promptba |
| `base-ref` | string | Diff alap referenciája |
| `head-ref` | string | Diff fej referenciája |
| `agent` | string | codex \| copilot |
| `agent-config` | objektum vagy JSON string | Egyedi ügynök konfiguráció |
| `context-lines` | szám | Kontextussorok száma |
| `max-diff-bytes`, `max-doc-bytes`, `max-file-bytes`, `snippet-max-chars` | szám | Méretkorlátok |
| `out` | string | Kimenet fájl elérési útja |

A config betöltés kikapcsolható a `--no-config` kapcsolóval.

---

## Használat

```bash
coderev <branch> [opciók]
```

Vagy telepítés nélkül (a projekt könyvtárából):

```bash
python -m coderev <branch> [opciók]
python coderev.py <branch> [opciók]   # visszafelé kompatibilis
```

---

## Argumentumok

### Kötelező pozicionális

| Argumentum | Leírás |
|------------|--------|
| `branch` | A vizsgálandó ág neve. A script ezt az ágat checkoutolja, majd a diff-et ehhez képest számítja. |

---

### Konfiguráció

| Argumentum | Leírás |
|------------|--------|
| `--config`, `-c` | Konfigurációs fájl elérési útja (JSON). Ha nincs megadva, automatikus keresés (lásd fent). |
| `--no-config` | Ne töltse be a konfigurációs fájlt. |

---

### Diff referencia

| Argumentum | Alapérték | Környezeti változó | Leírás |
|------------|-----------|--------------------|--------|
| `--base-ref` | `origin/main` | `coderev_BASE_REF` | Az összehasonlítás alapszáma. Ha `origin/` vagy `upstream/` prefixű, a megfelelő remote előzetesen fetchölve lesz. |
| `--head-ref` | `HEAD` | — | A diff „fej” referenciaja. Általában a branch legutolsó commitja. |

---

### Dokumentáció és sablon

| Argumentum | Alapérték | Környezeti változó | Leírás |
|------------|-----------|--------------------|--------|
| `--obey-doc` | *(üres)* | — | A promptba bevett, kötelezően betartandó dokumentum. Többször megadható. Fájl elérési út a repóhoz képest (vagy abszolút). |
| `--template` | *(üres)* | `coderev_TEMPLATE` | Sablonfájl elérési útja. Ha megadva, az AI ennek a sablonnak megfelelő formában adja vissza az eredményt. |

---

### AI ügynök (agent)

| Argumentum | Alapérték | Környezeti változó | Leírás |
|------------|-----------|--------------------|--------|
| `--agent` | `codex` | `coderev_AGENT` | Beépített ügynök: `codex` (Codex exec, stdin) vagy `copilot` (prompt fájlt vár). |
| `--agent-config` | *(üres)* | `coderev_AGENT_CONFIG` | Egyedi ügynök JSON konfiguráció. Felülírja a `--agent` értékét. Példa: `'{"name":"codex","mode":"stdin","cmd":["codex","exec","-"]}'` – `mode`: `stdin` / `arg` / `file`; `cmd`-ben `{prompt}` illetve `{prompt_file}` helyettesítéssel. |

---

### Kontextus és méretkorlátok

| Argumentum | Alapérték | Környezeti változó | Leírás |
|------------|-----------|--------------------|--------|
| `--context-lines` | `20` | `coderev_CONTEXT_LINES` | A diff hunkok körül megadandó kontextussorok száma. Ezek bekerülnek a promptba. |
| `--include-full-files` | *(ki)* | — | Ha be van kapcsolva, a változtatott fájlok teljes tartalma is belekerül a promptba (a `--max-file-bytes` korlátig). |
| `--max-diff-bytes` | `600000` | `coderev_MAX_DIFF_BYTES` | A diff maximális mérete byte-ban. A limit feletti rész csonkítva lesz. |
| `--max-doc-bytes` | `200000` | `coderev_MAX_DOC_BYTES` | Egy `--obey-doc` vagy `--template` fájl maximális mérete byte-ban. |
| `--max-file-bytes` | `200000` | `coderev_MAX_FILE_BYTES` | Egy fájl maximális mérete byte-ban, ha `--include-full-files` használva van. |
| `--snippet-max-chars` | `25000` | `coderev_SNIPPET_MAX_CHARS` | Egy fájl kontextussnippetének maximális hossza karakterben. |

---

### Kimenet

| Argumentum | Alapérték | Környezeti változó | Leírás |
|------------|-----------|--------------------|--------|
| `--out` | *(üres)* | `coderev_OUT` | Fájl elérési út. Ha megadva, az AI válasza ide kerül mentésre (a repó gyökeréhez képest vagy abszolút út). Relatív esetén a könyvtár szükség esetén létrejön. |

---

## Példák

```bash
# Egyszerű futtatás: feature/xyz ág ellenőrzése origin/main alapján
coderev feature/xyz

# Fejlesztői ág alapján, Codex-szel
coderev feature/xyz --base-ref origin/develop --agent codex

# Dokumentummal és sablonnal, eredmény fájlba
coderev feature/xyz --obey-doc CONTRIBUTING.md --template review_template.md --out review.md

# Teljes fájlokkal, több dokumentummal
coderev feature/xyz --obey-doc docs/style.md --obey-doc docs/api.md --include-full-files
```
