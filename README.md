# Astro code

## Push changes to the remote

Remote: `origin` → `file:///media/irene/linux/repo` (bare repo on this machine).

```bash
cd /media/irene/linux/cccc/astro/code
git status
git add <paths>              # e.g. shared_lib modules_data_processor/some-module
git commit -m "Short message"
git push                     # after first push: upstream is set; else: git push -u origin main
```

First time on a new branch: `git push -u origin <branch-name>`.

### `shared_lib` only (push)

```bash
cd /media/irene/linux/cccc/astro/code
git status shared_lib/
git add shared_lib/
git commit -m "Describe the shared_lib change"
git push origin main
```

---

## Clone and pull (for others)

**Same machine** (path to the bare repo is readable):

```bash
git clone file:///media/irene/linux/repo astro-code
cd astro-code
# later updates:
git pull origin main
```

`git pull` updates the **whole** clone (not a single folder). After a pull, your `shared_lib/` tree matches `main` on the remote for that path.

### `shared_lib` (pull latest after someone else pushed)

```bash
cd /media/irene/linux/cccc/astro/code   # or your clone path
git pull origin main
# optional: see what changed under shared_lib
git log -n 5 --oneline -- shared_lib/
```

Use the branch you track (often `main`; check with `git branch -a`).

---

## Stop tracking a path already on `main` (e.g. `shared_lib/old/`)

`.gitignore` only affects **untracked** files. Paths that were **already committed** stay on the branch until you remove them from the index (files can stay on disk).

Ensure the path is listed in `.gitignore` (e.g. `old/` in `shared_lib/.gitignore`), then:

```bash
cd /media/irene/linux/cccc/astro/code
git rm -r --cached shared_lib/old/
git commit -m "Stop tracking shared_lib/old; keep ignored locally"
git push origin main
```

`git rm -r --cached` drops tracking but does **not** delete the folder from your working tree.

**Another computer** cannot use `file:///media/irene/linux/...` on your disk. Put the bare repo on a shared path, serve it over SSH/Git, or mirror to GitHub/GitLab and share that URL; then clone with that URL and `git pull` as usual.
