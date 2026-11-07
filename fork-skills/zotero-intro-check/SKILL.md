---
name: zotero-intro-check
description: Fact-check the user's own manuscript text (usually an Introduction or Related Work section) against the papers in a Zotero collection - verify every claim about what a cited paper proposed, how a later paper improved on an earlier one, numbers, dates and "first to" statements, with page-cited evidence from the PDFs. Use when the user asks to check, verify or 核对 their intro/related work/文献综述 against Zotero, or asks whether their description of papers A, B, C is accurate.
---

# Intro fact-check against a Zotero collection

Uses `zotero-cli` (see the `zotero-cli` skill for the full command set). Always
pass `--json` when you will parse the output.

## Inputs

1. **The text**: a file path (`.tex`, `.md`, `.docx`, `.txt`) or pasted text.
   For `.tex`, keep `\cite{...}` keys - they are the most reliable link to items.
2. **The collection**: a name or key. Resolve with `zotero-cli --json get collections`;
   include subcollections when the user names a parent.

If either is missing, ask once, briefly.

## Procedure

1. **List the collection**: `zotero-cli --json get collection-items KEY --detail summary`
   (page with `--offset` if large). Note which items have a PDF.

2. **Split the text into atomic claims.** One claim = one checkable statement
   about a cited work, e.g.
   - "A proposed method M" / "A was the first to ..."
   - "B improved A by doing X" (two facts: B does X; B builds on/compares with A)
   - numbers (accuracy, dataset size, speed-up), years, venues
   - chronology implied by "then", "later", "subsequently"
   Keep the original sentence with each claim.

3. **Map each citation to an item**, in this order:
   - `\cite{key}` → `zotero-cli --json search --mode citekey KEY`
   - "Author et al. (Year)" / "Author et al. [n]" → match first-author surname + year
     inside the collection first, then `zotero-cli --json search "Surname Year"` library-wide.
   - No match → report as 不在库中; offer `zotero-cli add doi ...` if the user wants it.
   - Item without PDF → run `zotero-cli fetch-pdf KEY` (it is safe: it only
     attaches a missing PDF). If that fails, check against abstract only and say so.

4. **Find evidence - targeted, not whole papers:**
   - `zotero-cli --json get metadata KEY` (abstract, date, venue)
   - `zotero-cli --json read KEY --find "<distinctive term>"` to locate passages
   - `zotero-cli --json outline KEY`, then `read KEY --start-page N --end-page M`
     for the abstract / contributions list / method section / results table
   - For "B improves A": confirm B's text names A (or A's method) as its
     baseline or starting point - `read B --find "<A's method name or first author>"`.
     Check B's date is after A's.
   Quote the exact supporting sentence and its page number. Never infer a
   verdict from memory of the paper; if the PDF does not say it, it is 无依据.

5. **Verdict per claim** (use these exact labels):
   - ✅ 准确 - the paper states it
   - ⚠️ 不够准确 - right idea but wrong detail, overstated, or attributes to the wrong paper
   - ❌ 与原文矛盾
   - ❓ 原文中找不到依据
   - 📭 无法核对 - not in library / no full text (say which)

## Output (in Chinese; quotes stay in the paper's original language)

1. One-line summary: N claims checked, counts per verdict.
2. A table, in the order claims appear:

   | # | 原句（节选） | 引用文献 | 判定 | 原文证据（页码 + 引文） | 建议改法 |

3. For every ⚠️/❌, a suggested rewrite of the sentence that the evidence supports,
   keeping the user's style and citation format.
4. Chronology/lineage check: one line per "A → B → C" chain, confirming order and
   that each step actually builds on the previous one.
5. Housekeeping found along the way (do not fix silently): cited arXiv version that
   has a published version, duplicates, items with missing PDF that could not be fetched.

## Rules

- Do not edit the user's manuscript unless asked; give rewrites in the report.
- Do not write notes/tags to Zotero unless asked. If the user wants the report kept,
  `zotero-cli notes create --item-key ... --text -` on a relevant item, or save a
  Markdown file next to the manuscript.
- Read pages, not whole PDFs: a full paper is ~10k tokens. For more than ~15
  cited papers, work through them in batches and keep a running table.
