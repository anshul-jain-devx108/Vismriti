# Submission checklist

Work top to bottom. Anything unchecked at submission time should be removed
from the README rather than left as a claim, because a broken link or an
unbacked assertion costs more than the missing item would have earned.

---

## Hard requirements

- [ ] **Public repository.** <https://github.com/anshul-jain-devx108/Vismriti> is
      public and the default branch has the final commit.
- [ ] **License visible.** Apache-2.0 in `LICENSE` and showing in the GitHub
      About panel.
- [ ] **Demo video.** Three minutes, unlisted or public on YouTube, link pasted
      into the README where the placeholder sits. Follow
      [`demo_script.md`](demo_script.md).
- [ ] **Devpost form.** Text ready in [`devpost_submission.md`](devpost_submission.md).
- [ ] **AI disclosure.** The README carries one. Keep it. Devpost rules
      generally require disclosing model assistance, and an accurate disclosure
      costs nothing while a missing one can invalidate an entry.

---

## Verify before you record

Run these in a clean shell. Each should pass without editing anything.

```bash
./.venv/bin/python -m pytest tests/ -q
```
- [ ] 8 passed.

```bash
./.venv/bin/erase plan --email priya.sharma@example.com --fixtures
```
- [ ] Prints the action table. Note the asset and residual counts.

```bash
./.venv/bin/erase run --email priya.sharma@example.com --fixtures --approve --dry-run
```
- [ ] Writes a Markdown report and a JSON audit trail under `runs/`.
- [ ] Open the report. Confirm it does not claim a DataHub write that did not
      happen.

```bash
./.venv/bin/python scripts/verify_live_datahub.py
```
- [ ] Reads the seeded entities off the live GMS.

```bash
./.venv/bin/erase plan --email priya.sharma@example.com
```
- [ ] Live mode produces a plan. Note the count and whether it matches fixture
      mode.

```bash
./.venv/bin/python run.py --no-reload
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7777/docs
```
- [ ] HTTP 200.

```bash
./.venv/bin/streamlit run src/vismriti/ui/app.py
```
- [ ] Renders in a browser.

```bash
./.venv/bin/python -m ruff check src/
```
- [ ] Clean, or only warnings you have consciously accepted.

---

## Every link in the README resolves

A judge clicking a dead link concludes the rest is decorative too. Check each
one, including the relative paths.

```bash
grep -oE '\]\(([^)h][^)]*)\)' README.md | tr -d '])(' | sort -u | \
  while read f; do [ -e "${f%%#*}" ] || echo "MISSING: $f"; done
```

- [ ] No output from the command above.
- [ ] External URLs open: the repo, the GMS endpoint, the DataHub UI, the
      evidence sources.
- [ ] Any file referenced that does not exist has either been created or had its
      claim deleted from the README.

---

## Claims match reality

Read the README as a hostile reviewer. For each claim, name the file and line
that backs it.

- [ ] Every listed interface actually runs. If Slack is not wired for the demo,
      say what was demonstrated rather than implying all four were.
- [ ] Counts are current. If the plan yields a different number of assets than
      the README table shows, fix the README.
- [ ] The write-back section describes what happens on the deployment you are
      demoing against, including failure when writes cannot land.
- [ ] No section claims a directory or artifact the repository does not contain.
- [ ] The architecture description matches the code after the final commit.

---

## Repository hygiene

```bash
git status --short
git check-ignore -v .env
```

- [ ] `.env` is ignored and was never committed. If it was, rotate every secret
      in it before making the repo public.
- [ ] No API keys, bot tokens, or connection strings anywhere in tracked files.
      Check `.env.example` holds placeholders only.
- [ ] `runs/` and other local artifacts are not committed.
- [ ] The final commit message describes the state, and the tree builds from a
      fresh clone:

```bash
cd $(mktemp -d) && git clone <repo> t && cd t && \
  uv venv --python 3.12 .venv && \
  uv pip install --python .venv/bin/python -e ".[dev]" && \
  ./.venv/bin/python -m pytest tests/ -q
```

- [ ] Fresh clone passes. This catches files that only exist on your machine,
      which is the single most common way a working project fails for a judge.

---

## Recording the video

- [ ] Terminal font large enough to read at 720p.
- [ ] `rm -rf runs/` first so the artifacts on screen are from this run.
- [ ] Fixture mode for the main flow, so nothing depends on network conditions
      during the recording.
- [ ] Show the residual-risk row and say why it matters. That is the argument.
- [ ] If asked or if it comes up, state the deployment limitation plainly
      rather than editing around it.
- [ ] Under three minutes.

---

## Last pass

- [ ] README opens with what the project does, not with how it was built.
- [ ] Someone who has never seen the repo can go from clone to a working plan
      using [`../SETUP.md`](../SETUP.md) alone.
- [ ] You can answer, in one sentence each: what it does, why lineage at request
      time is necessary, and what it refuses to do on its own.
