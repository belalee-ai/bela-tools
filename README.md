<!-- bela-skills-migration -->
> 本仓库的相关 Skill 已收录到 [Bela Skills](https://github.com/belalee-ai/bela-skills)。推荐用 `npx skills add belalee-ai/bela-skills` 安装。
>
> 合集入口为 [`bela-github-publish`](https://github.com/belalee-ai/bela-skills/tree/main/bela-github-publish)、[`bela-skill-hygiene`](https://github.com/belalee-ai/bela-skills/tree/main/bela-skill-hygiene)。当前仓库保留旧版文档、链接和提交历史。

[中文版](./README.zh-CN.md)

# bela-tools

Two things I kept doing manually until they became skills.

One is publishing a project to GitHub — the scan, the README structure, the git workflow, the post-publish checks. The other is auditing Claude Code skills before the list gets so long the wrong one fires.

Both live here now.

---

## What's inside

**GitHub Publish** — say `publish to GitHub` or `发布到 GitHub`

Walks through the full publish flow: project structure check, privacy scan, bilingual README requirements, git init and push, post-publish verification. Covers the edge cases that burn you the first time — SSH fallback, gitignore for private data, naming conventions.

**Skill Hygiene** — say `skill audit` or `check skill overlap`

Before you install a new skill, run a duplicate check. When your skill count climbs past 30 and selection starts going wrong, run the full audit. Finds overlaps, suggests merges, soft-deletes bloat without losing history.

---

## Install

```bash
git clone https://github.com/belalee-ai/bela-tools.git ~/.claude/plugins/bela-tools
```

Restart Claude Code. Done.

**Other ways to install**

- `/plugin install https://github.com/belalee-ai/bela-tools` — if your Claude Code version supports it
- `/find-skills bela-tools` — if you have Superpowers installed
- [Download ZIP](https://github.com/belalee-ai/bela-tools/archive/refs/heads/main.zip), unzip to `~/.claude/plugins/bela-tools/`, restart

---

## Privacy

No profile data involved in these skills. The skill-hygiene audit script runs locally against your `~/.claude/skills/` directory and writes to `~/.claude/skills-audit.log`. Nothing leaves your machine.

---

## Repo layout

```
.claude-plugin/plugin.json              version, keywords, author
skills/
  bela-github-publish/SKILL.md          full GitHub publish workflow (6 phases)
  skill-hygiene/SKILL.md                skill audit and overlap detection
    scripts/skill-audit.sh              local audit script
```
