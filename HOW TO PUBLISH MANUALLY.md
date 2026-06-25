# HOW TO PUBLISH VISTAS TERMINAL — manually, from the terminal (no Claude needed)

This is the **independent, do-it-yourself** guide to building the Vistas Terminal and pushing it
live. You never need me (Claude) to publish — everything below is plain terminal commands you run
yourself. Read it once and you own the whole publish pipeline.

- **What goes live:** the hosted Terminal site, mirrored into the `terminal/` folder of the
  GitHub-Pages repo **`kartiksngh/vistas`**.
- **Live URL:** <https://kartiksngh.github.io/vistas/terminal/>
- **Where you run these:** a terminal (PowerShell) opened at the project root
  `C:\Users\Administrator\Documents\Projects\Vistas`.
- **The publish repo** is a *separate* git repo that lives inside this folder as **`_pages\`**
  (its own `.git`, remote = `git@github.com:kartiksngh/vistas.git`). The build is copied into
  `_pages\terminal\` and pushed from there.

---

## The one rule that keeps it robust

**Never run two builds or publishes at the same time.** Daily Refresh, Publish Last Build, a manual
`python publish_terminal.py`, or me building in the background — each of these writes the **same**
files (`output\terminal_site\` and `_pages\`). Two at once corrupt each other (that is the only way
this "breaks"). **Run one, wait for it to fully finish, then run the next.** If you're unsure whether
one is still running, see "Is something already building?" at the bottom.

Your safety net: the publisher **validates the build before pushing**. If the build is broken it
prints `FAULTY SHELL — NOT PUBLISHING` and **leaves the live site unchanged**. A failed publish never
damages what's already live.

---

## A) The easy way — one command

From the project root:

```powershell
# rebuild from the data already on disk, validate, and push live:
python publish_terminal.py --no-fetch

# OR: pull fresh NSE prices first, then rebuild + validate + push:
python publish_terminal.py

# OR: the build on disk is already good — just validate + push it (fastest, no rebuild):
python publish_terminal.py --no-rebuild
```

Watch the output. Success ends with `DONE.` and `published OK`. If it ends with
`FAULTY SHELL — NOT PUBLISHING`, nothing was pushed — see **Troubleshooting**.

The double-click `.bat` shortcuts in `pipeline\` do exactly these:
- **`Daily Refresh Vistas.bat`** = `python -m vistas.pipeline` → full refresh + rebuild + validate + **auto-push**.
- **`Nightly Build (no publish).bat`** = same but `--no-push` → builds + validates, does **not** push.
- **`Publish Last Build.bat`** = `python publish_terminal.py --no-rebuild` → validate the build on disk + push.

---

## B) The fully-manual way — every step by hand (git in the terminal)

Use this when you want to do the commit/push yourself, or A failed and you want control.

### Step 1 — build the site and validate it (no push)
```powershell
python publish_terminal.py --no-fetch --no-push
```
This rebuilds `output\terminal_site\` and runs the validator. **Only continue if you see
`Validation passed.`** If it says `FAULTY SHELL`, stop and fix it (Troubleshooting) — do not push a
broken build.

### Step 2 — mirror the built site into the publish repo
```powershell
robocopy "output\terminal_site" "_pages\terminal" /MIR /NFL /NDL /NP /NJH /NJS /R:1 /W:1
```
`robocopy /MIR` makes `_pages\terminal\` an **exact copy** of the fresh build (adds new files,
updates changed ones, deletes any that no longer exist). **robocopy exit codes 0–7 mean success**
(8 or higher is a real error — PowerShell shows it as `$LASTEXITCODE`).

### Step 3 — commit and push from inside the publish repo
```powershell
cd _pages
git add -A
git commit -m "terminal v2 site refresh"
git push origin main
cd ..
```
The site goes live at <https://kartiksngh.github.io/vistas/terminal/> within ~1 minute (the **first**
publish after a Pages re-enable can take a few minutes).

If `git commit` prints **"nothing to commit"**, the site is identical to what's already live — there
is nothing to push, and that is **not** an error.

---

## C) The live link is dead / 404 after you toggled the repo private ↔ public

This is a **GitHub setting**, not your build. On a free GitHub account, **GitHub Pages only serves
PUBLIC repos** — the moment you flip `kartiksngh/vistas` to **private**, Pages is **disabled** and the
link 404s. Flipping back to **public does NOT automatically turn Pages back on**, and **pushing does
not re-enable it either.** You must re-enable it once, by hand:

1. Open GitHub → repo **`kartiksngh/vistas`** → **Settings** → **Pages**.
2. Under **Build and deployment → Source**, choose **"Deploy from a branch"**
   (NOT "GitHub Actions").
3. **Branch = `main`**, **Folder = `/ (root)`** → **Save**.
4. Wait 1–3 minutes, then reload <https://kartiksngh.github.io/vistas/terminal/>.

(If it still 404s after a few minutes, push any small commit from `_pages` to nudge a redeploy:
`cd _pages; git commit --allow-empty -m "redeploy"; git push origin main; cd ..`.)

**Bottom line:** toggling visibility never breaks your *pipeline* — it only switches Pages off, and
the four clicks above switch it back on.

---

## Troubleshooting

- **`FAULTY SHELL — NOT PUBLISHING`** — the validator found the build broken (a panel didn't render,
  or data was missing). The live site is **left unchanged** (safe). Almost always the cause is an
  **interrupted / concurrent build** that left partial data on disk. Fix: rebuild cleanly and
  re-validate before pushing:
  ```powershell
  python publish_terminal.py --no-fetch --no-push
  ```
  If it now says `Validation passed.`, publish with `python publish_terminal.py --no-rebuild`.

- **Funds tab looks empty in the validator** — the funds folder
  `output\terminal_site\data\funds_portfolio\` is empty or partial (a build was killed mid-way).
  Just rebuild (the line above) — a full rebuild repopulates it.

- **`git push` asks for a password or fails** — the publish repo pushes over **SSH**
  (`git@github.com:kartiksngh/vistas.git`). Make sure your SSH key is loaded (it normally is, since
  the `.bat`s push fine).

- **"nothing to commit"** — not an error; the build is identical to what's already live.

### Is something already building? (check before you start)
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CreationDate,
    @{N='cmd';E={($_.CommandLine -replace '\s+',' ')}}
```
If you see a `python ... publish_terminal` or `python -m vistas.pipeline` line, a build is running —
**wait for it to finish** before starting another. Stop a stuck one with
`Stop-Process -Id <ProcessId>` only if you're sure it's hung.

---

## What "publish" actually does (so nothing is a black box)

1. `python publish_terminal.py` calls `deck.save_terminal_site()` → writes the site to
   `output\terminal_site\` (the shell `index.html` + per-symbol JSON under `data\`).
2. It validates `output\terminal_site\index.html` with a headless-browser smoke-test. **Broken → stop.**
3. It `robocopy /MIR`s `output\terminal_site\` → `_pages\terminal\`.
4. Inside `_pages\` it runs `git add -A`, `git commit`, `git push origin main`.
5. GitHub Pages serves `_pages\terminal\` at the live URL.

That's the entire pipeline. Sections A and B above are just you driving those same five steps by hand.
