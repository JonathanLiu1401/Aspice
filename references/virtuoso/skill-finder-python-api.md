# SKILL Finder — Python API

VirtuosoBridge provides two independent tools for querying Cadence SKILL APIs:

| Tool | Purpose | Remote data source |
|------|---------|-------------------|
| **SKILL Finder** (`skill-find`) | Search SKILL functions by name | `doc/finder/SKILL/*.fnd` (name + syntax + description) |
| **More Info** (`skill-info`) | Get detailed docs for a specific function | `doc/api_more_info/api_more_info.tgf` + HTML docs |

Both are accessed via `VirtuosoClient.find_skill()` and `VirtuosoClient.get_skill_more_info()`.

---

## SKILL Finder (`skill-find`)

**Purpose:** Search SKILL functions by name. Returns function name, syntax signature, and one-line description.

**Data source:** Cadence SKILL Finder database `doc/finder/SKILL/*.fnd`. On first run the database is automatically downloaded to a local cache.

```python
from virtuoso_bridge import VirtuosoClient
client = VirtuosoClient.from_env()

# Default fuzzy search (case-insensitive substring)
results = client.find_skill("dbOpenCellView")

# Search modes
results = client.find_skill("dbOpenCellView", mode="exact")
results = client.find_skill("dbOpen", mode="prefix")     # name starts with dbOpen
results = client.find_skill("ViewByType", mode="suffix")  # name ends with ViewByType
results = client.find_skill("^db.*$", mode="regex")     # Python regex

# Limit results
results = client.find_skill("dbOpen", limit=10)

# Also search in descriptions (default: False — name-only)
results = client.find_skill("Returns the value", include_desc=True)
```

**Returns:** `List[SKILLEntry]`, each entry contains:
- `name`: function name
- `syntax`: function signature (Lisp-style parameter notation)
- `description`: one-line description
- `source`: source `.fnd` filename

**Search modes:**

| Mode | Description | Example |
|------|-------------|---------|
| `fuzzy` | Case-insensitive substring match (default) | `"dbOpen"` matches `"dbOpenCellViewByType"` |
| `prefix` | Name starts with query | `"dbOpen"` matches `"dbOpenCellView"` |
| `suffix` | Name ends with query | `"ViewByType"` matches `"dbOpenCellViewByType"` |
| `exact` | Name equals query exactly | `"dbOpenCellViewByType"` matches only that |
| `regex` | Python regular expression | `"^db.*$"` matches all names starting with `db` |

**Syntax signature format (Lisp-style):**

```scheme
dbOpenCellViewByType(
  { gt_lib | nil }       ; required: library name or nil
  t_cellName             ; required: cell name
  lt_viewName            ; required: view name
  [ t_viewTypeName ]    ; optional: view type
  [ t_mode ]            ; optional: mode
  [ d_contextCellView ] ; optional: context cellview
) => d_cellView / nil
```

- `{ a | b }` — choose one (usually required)
- `[ arg ]` — optional argument
- `=> ret / nil` — return value

---

## More Info (`skill-info`)

**Purpose:** Get detailed documentation for a SKILL function (Description / Arguments / Returns / Example / etc.).

**Data source:** Cadence More Info system — `doc/api_more_info/api_more_info.tgf` index + HTML docs (downloaded on demand).

```python
from virtuoso_bridge import VirtuosoClient
client = VirtuosoClient.from_env()

# Get detailed docs for a function
info = client.get_skill_more_info("absGetOption")

# Returns a dict:
# {
#   "func_name": "absGetOption",
#   "file_path": "$abstract/abstract_skill.html",
#   "topic": "absGetOption",
#   "raw_html": "<html>...</html>",
#   "plain_text": "### absGetOption\n..."  # markdown-formatted
# }

# Print as markdown
print(info["plain_text"])
```

**Return value format (`MoreInfoResult`):**

| Field | Type | Description |
|-------|------|-------------|
| `func_name` | `str` | Function name |
| `file_path` | `str` | HTML doc path (e.g. `$abstract/abstract_skill.html`) |
| `topic` | `str \| None` | Topic name from TGF index; NULL means whole file |
| `raw_html` | `str` | Raw HTML content |
| `plain_text` | `str` | Rendered as markdown-formatted plain text |

Use `skill-find` to locate other functions, then `skill-info` for details.

---

## CLI Usage

```bash
# Search SKILL functions
virtuoso-bridge skill-find dbOpenCellView
virtuoso-bridge skill-find "^db.*" --mode regex --limit 20

# Also search in descriptions (may return more noisy results)
virtuoso-bridge skill-find "open.*cellview" --mode regex --include-desc

# Get detailed docs
virtuoso-bridge skill-info absGetOption
virtuoso-bridge skill-info absGetOption --json   # JSON output
```
