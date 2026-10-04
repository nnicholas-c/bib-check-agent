"""Offline checks of the skill's files and manifests. Run: python tests/test_skill_files.py

Catches what the validators don't: files SKILL.md names but the repo lacks, names and versions
that drift apart across the four manifests, and rules that drift between the workflow prompts
(Claude Code) and the step-by-step procedure (every other agent)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "bib-check-agent"
SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
assert text.isascii(), "keep SKILL.md ASCII: Windows validators read it as cp1252"
front = text.split("---")[1]
keys = re.findall(r"^([a-z][\w-]*):", front, re.M)
assert set(keys) <= SPEC_FIELDS, f"non-spec frontmatter fields: {set(keys) - SPEC_FIELDS}"
field = lambda k: re.search(rf"^{k}: (.*)$", front, re.M).group(1).strip()
name, description = field("name"), field("description")
assert name == SKILL.name and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
assert 0 < len(description) <= 1024 and "<" not in description and ">" not in description, "description breaks claude.ai upload"
assert len(field("compatibility")) <= 500
assert field("allowed-tools").startswith("'"), "allowed-tools must be one quoted string (spec and gh skill publish)"
version = re.search(r'^\s+version: "([^"]+)"', front, re.M).group(1)

# Every scripts/, references/ and workflows/ path SKILL.md mentions exists
for rel in sorted(set(re.findall(r"\b((?:scripts|references|workflows)/[\w./-]+\.\w+)", text))):
    assert (SKILL / rel).is_file(), f"SKILL.md names {rel}, which does not exist"
assert (SKILL / "scripts" / "requirements.txt").is_file()

# Manifests agree on name and version; the root manifest fits the Agent Plugins 1.0 schema
root = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
assert set(root) <= {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
assert root["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
assert re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", root["name"])
entry = market["plugins"][0]
assert root["name"] == claude["name"] == entry["name"] == name, "plugin names differ"
assert root["version"] == claude["version"] == version, "versions differ between SKILL.md and plugin manifests"
assert entry["source"] == "./" and "claude" not in market["name"] and "anthropic" not in market["name"]

# The rules both paths depend on read the same in the workflow prompts and the procedure
js = (SKILL / "workflows" / "investigate.js").read_text(encoding="utf-8")
proc = (SKILL / "references" / "procedure.md").read_text(encoding="utf-8")
for phrase in ("never from memory", "--bibtex", "--search", "published version", "latest revision",
               "% FABRICATED: no real source found", "% UNRESOLVED", ".bak2", "% Checked by verify_bib.py",
               "Citation keys are the only text you may change in .tex files",
               "flags again by design", "A preprint and its published version", "Don't add new entries", "latexmk -v", "matching whole keys", "likely fabricated", "at least two independent places", "never try to get around a CAPTCHA"):
    assert phrase in js, f"investigate.js lacks: {phrase}"
    assert phrase in proc, f"procedure.md lacks: {phrase}"
assert "references/procedure.md" in text, "SKILL.md must point other agents to the procedure"
print("ok")
