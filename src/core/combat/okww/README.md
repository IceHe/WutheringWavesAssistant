# OKWW combat backend

The built-in character routines, registry, and combat templates come from [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves) at commit `fac98810b2ee4c7b803e386094e40291cd20376a`. The AGPL v3 license is in the WWA repository root.

- `characters/`, `registry.py`, and `labels.py` are adapted from OKWW's `src/char/` and `src/Labels.py`. The registry contains 57 implementations and 66 portrait labels. Character-specific settings use OKWW's defaults. Iuno, Roccia, Chisa, and Suoming use the standalone app's default resonance-chain and role options.
- `rotation.py` adapts the cooldown, concerto, and rotation methods from OKWW's `src/task/BaseCombatTask.py`. The adapter confirms switching against the WWA frame, rejects downed targets, and bounds switch attempts.
- `backend.py`, `runtime.py`, `inputs.py`, and `vision.py` connect the routines to WWA's task lifetime, window, screenshots, and OCR. `post_message.py` follows the battle input sequence of [ok-script 2.0.20](https://pypi.org/project/ok-script/2.0.20/) using WWA's game window and keybindings. Runtime dependencies are supplied by WWA.
- `templates/` contains the matching assets used by the character registry and combat code. Template filenames retain source resolution and location. The HUD replay fixtures under `tests/core/okww_frames/` contain relevant regions from the upstream `tests/images/` recordings at the same revision.

Run the focused replay and lifecycle checks from the WWA project root:

```powershell
.\py312\python.exe -B -m unittest -v tests.core.okww_combat_test
```

Update the branch with `git fetch origin` followed by `git rebase origin/main`. The release updater may switch branches or reset the checkout.
