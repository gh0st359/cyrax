"""
CYRAX Skill Manager

Discovers, loads, and invokes Markdown-based skills from project/global dirs.

Skills are Markdown files with YAML frontmatter that define reusable workflows
or domain-specific knowledge that CYRAX can load on-demand.

Search paths (in priority order):
  1. .cyrax/skills/<name>/SKILL.md   (project-level)
  2. ~/.cyrax/skills/<name>/SKILL.md  (user-level / global)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Skill:
    """A loaded skill definition."""
    name: str
    description: str
    content: str
    path: Path
    trigger: str = ""
    tools: list[str] = field(default_factory=list)
    auto_load: bool = False


class SkillManager:
    """Discovers and manages CYRAX skills."""

    SKILL_FILENAME = "SKILL.md"

    def __init__(self, project_root: Optional[str] = None):
        self._skills: dict[str, Skill] = {}
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._search_paths = self._build_search_paths()
        self.discover()

    def _build_search_paths(self) -> list[Path]:
        """Build the ordered list of directories to search for skills."""
        paths = []
        # Project-level
        paths.append(self._project_root / ".cyrax" / "skills")
        # User-level
        home = Path.home()
        paths.append(home / ".cyrax" / "skills")
        # Also support .agents/skills/ for cross-tool compatibility
        paths.append(self._project_root / ".agents" / "skills")
        return paths

    def discover(self) -> dict[str, Skill]:
        """Scan all search paths and load skill definitions."""
        self._skills.clear()
        for search_dir in self._search_paths:
            if not search_dir.is_dir():
                continue
            for entry in sorted(search_dir.iterdir()):
                if not entry.is_dir():
                    continue
                skill_file = entry / self.SKILL_FILENAME
                if skill_file.is_file():
                    skill = self._load_skill(entry.name, skill_file)
                    if skill and skill.name not in self._skills:
                        self._skills[skill.name] = skill
        return self._skills

    def _load_skill(self, dir_name: str, path: Path) -> Optional[Skill]:
        """Parse a SKILL.md file into a Skill object."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        # Parse YAML frontmatter
        frontmatter = {}
        body = text
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if fm_match:
            raw_fm = fm_match.group(1)
            body = text[fm_match.end():]
            for line in raw_fm.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    frontmatter[key.strip()] = (
                        value.strip().strip('"').strip("'")
                    )

        name = frontmatter.get("name", dir_name)
        description = frontmatter.get("description", "")
        trigger = frontmatter.get("trigger", "")
        tools_raw = frontmatter.get("tools", "")
        tools = (
            [t.strip() for t in tools_raw.split(",") if t.strip()]
            if tools_raw else []
        )
        auto_load = (
            frontmatter.get("auto_load", "").lower()
            in ("true", "yes", "1")
        )

        return Skill(
            name=name,
            description=description,
            content=body.strip(),
            path=path,
            trigger=trigger,
            tools=tools,
            auto_load=auto_load,
        )

    def list_skills(self) -> list[dict]:
        """Return a summary list of available skills."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "path": str(s.path),
                "auto_load": s.auto_load,
                "tools": s.tools,
            }
            for s in self._skills.values()
        ]

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def invoke_skill(self, name: str) -> Optional[str]:
        """Load a skill's full content for injection into context."""
        skill = self._skills.get(name)
        if not skill:
            return None
        return f"[Skill: {skill.name}]\n{skill.content}"

    def get_auto_load_context(self) -> str:
        """Get combined auto-load skill content for the system prompt."""
        parts = []
        for skill in self._skills.values():
            if skill.auto_load:
                parts.append(f"[Skill: {skill.name}]\n{skill.content}")
        return "\n\n".join(parts)

    def match_skills_for_context(self, user_message: str) -> list[Skill]:
        """Find skills whose trigger matches the user's message."""
        matched = []
        msg_lower = user_message.lower()
        for skill in self._skills.values():
            if skill.auto_load:
                continue  # Already loaded
            if skill.trigger and skill.trigger.lower() in msg_lower:
                matched.append(skill)
        return matched

    def create_skill(
        self,
        name: str,
        description: str,
        content: str,
        project_level: bool = True,
    ) -> Path:
        """Create a new skill file."""
        if project_level:
            base = self._project_root / ".cyrax" / "skills"
        else:
            base = Path.home() / ".cyrax" / "skills"

        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / self.SKILL_FILENAME

        frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
        skill_file.write_text(frontmatter + content, encoding="utf-8")

        # Reload
        self.discover()
        return skill_file
