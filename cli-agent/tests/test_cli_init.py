"""medulla init and refresh: deploying a template, scaffolding, re-syncing.

What init writes is what every later run resolves, so where it writes matters as much
as what.
"""
import fcntl
import json
import os
from pathlib import Path

from conftest import write_workflow as setup
from medulla.v2.cli import main as cli_main
from medulla.v2.engine import find_resumable, run_workflow


def test_init_without_name_prints_usage(tmp_path, monkeypatch, capsys):
    import medulla.cli as shim
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init"])
    assert shim.entry() == 1
    err = capsys.readouterr().err
    assert "usage: medulla init <name>" in err and "spar" in err


def test_init_deploys_bundled_template(tmp_path, monkeypatch):
    # A template installs machine-wide by default: same file everywhere, one to update.
    # HOME is redirected so the suite never writes into the real one.
    import medulla.cli as shim
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init", "spar"])
    assert shim.entry() == 0
    assert not (tmp_path / ".medulla" / "workflows" / "spar" / "workflow.yaml").exists()
    pdir = home / ".medulla" / "workflows" / "spar"
    assert (pdir / "workflow.yaml").is_file()
    assert (pdir / "prompts" / "spar.md").is_file()
    assert ".env" in (pdir / ".gitignore").read_text()
    assert not (pdir / "runs").exists()                  # template noise excluded


def test_init_scaffolds_a_workflow(tmp_path, monkeypatch):
    import medulla.cli as shim
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init", "my-pipe"])
    assert shim.entry() == 0
    pdir = tmp_path / ".medulla" / "workflows" / "my-pipe"
    assert (pdir / "workflow.yaml").is_file()
    assert (pdir / "README.md").is_file()
    assert ".env" in (pdir / ".gitignore").read_text()
    assert (pdir / "prompts").is_dir()
    # the scaffold must be a VALID, runnable workflow out of the box
    assert run_workflow(pdir / "workflow.yaml", workdir=tmp_path) == 0
    # re-init refuses to clobber
    monkeypatch.setattr("sys.argv", ["medulla", "init", "my-pipe"])
    assert shim.entry() == 1


def test_init_skill_flag(tmp_path, monkeypatch):
    import medulla.cli as shim
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init", "spar", "--skill"])
    assert shim.entry() == 0
    # machine-wide skill dirs: usable from every repo, no per-checkout install
    for root in (home / ".claude" / "skills", home / ".agents" / "skills",
                 home / ".config" / "opencode" / "skills"):
        skill = root / "spar" / "SKILL.md"
        text = skill.read_text()
        assert skill.is_file() and "spar" in text
        assert "medulla launch spar start" in text
        assert "medulla launch spar --apple" not in text
    # a scaffold is this repo's own work: it stays local, skill included
    monkeypatch.setattr("sys.argv", ["medulla", "init", "fresh", "--skill"])
    assert shim.entry() == 0
    assert (tmp_path / ".medulla" / "workflows" / "fresh" / "SKILL.md").is_file()


def test_init_local_flag_keeps_everything_in_the_project(tmp_path, monkeypatch):
    import medulla.cli as shim
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init", "spar", "--skill", "--local"])
    assert shim.entry() == 0
    assert (tmp_path / ".medulla" / "workflows" / "spar" / "workflow.yaml").is_file()
    assert (tmp_path / ".claude" / "skills" / "spar" / "SKILL.md").is_file()
    assert not (home / ".medulla" / "workflows" / "spar").exists()
    assert not (home / ".claude" / "skills" / "spar").exists()


def test_init_apple_skill_switch_survives_refresh(tmp_path, monkeypatch):
    import medulla.cli as shim
    from medulla.init import _bundle_dir, refresh_skill

    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["medulla", "init", "spar", "--skill", "--apple"])
    assert shim.entry() == 0

    workflow_dir = home / ".medulla" / "workflows" / "spar"
    skill_paths = [workflow_dir / "SKILL.md"]
    skill_paths.extend(root / "spar" / "SKILL.md" for root in (
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        home / ".config" / "opencode" / "skills",
    ))
    for skill in skill_paths:
        text = skill.read_text()
        assert "medulla launch spar --apple start" in text
        assert "medulla launch spar start" not in text

    bundle = _bundle_dir("spar")
    assert bundle is not None
    assert "medulla launch spar start" in (bundle / "SKILL.md").read_text()

    assert refresh_skill("spar", str(tmp_path)) == 0
    for skill in skill_paths:
        assert "medulla launch spar --apple start" in skill.read_text()

    custom = (workflow_dir / "workflow.yaml").read_text() + "\n# keep me\n"
    (workflow_dir / "workflow.yaml").write_text(custom, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["medulla", "init", "spar", "--skill"])
    assert shim.entry() == 0
    assert (workflow_dir / "workflow.yaml").read_text() == custom
    for skill in skill_paths:
        text = skill.read_text()
        assert "medulla launch spar start" in text
        assert "medulla launch spar --apple" not in text


def test_init_local_apple_skill_stays_local(tmp_path, monkeypatch):
    import medulla.cli as shim

    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["medulla", "init", "spar", "--skill", "--apple", "--local"])
    assert shim.entry() == 0
    for root in (tmp_path / ".medulla" / "workflows",
                 tmp_path / ".claude" / "skills",
                 tmp_path / ".agents" / "skills",
                 tmp_path / ".opencode" / "skills"):
        assert "medulla launch spar --apple start" in (root / "spar" / "SKILL.md").read_text()
    assert not (home / ".medulla" / "workflows" / "spar").exists()


def test_init_skill_refuses_source_or_destination_symlink(tmp_path, monkeypatch):
    import medulla.cli as shim

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["medulla", "init", "spar", "--local"])
    assert shim.entry() == 0
    source = tmp_path / ".medulla" / "workflows" / "spar" / "SKILL.md"
    source_text = source.read_text()
    victim = tmp_path / "victim.md"
    victim.write_text(source_text, encoding="utf-8")
    source.unlink()
    source.symlink_to(victim)

    monkeypatch.setattr(
        "sys.argv", ["medulla", "init", "spar", "--skill", "--apple", "--local"])
    assert shim.entry() == 1
    assert victim.read_text() == source_text

    source.unlink()
    source.write_text(source_text, encoding="utf-8")
    dest = tmp_path / ".claude" / "skills" / "spar"
    dest.mkdir(parents=True)
    target_victim = tmp_path / "target-victim.md"
    target_victim.write_text("safe", encoding="utf-8")
    (dest / "SKILL.md").symlink_to(target_victim)

    assert shim.entry() == 1
    assert target_victim.read_text() == "safe"


def test_refresh_scans_and_updates(tmp_path, monkeypatch):
    import medulla.cli as shim
    # stale workflow deploy (+ a run to preserve) and stale SKILL.md copy
    wf = tmp_path / "projA" / ".medulla" / "workflows" / "spar"
    (wf / "runs" / "old").mkdir(parents=True)
    wf.joinpath("workflow.yaml").write_text("STALE", encoding="utf-8")
    wf.joinpath("runs", "old", "keep").write_text("x", encoding="utf-8")
    sk = tmp_path / "projB" / ".claude" / "skills" / "spar"
    sk.mkdir(parents=True)
    sk.joinpath("SKILL.md").write_text("STALE", encoding="utf-8")
    pruned = tmp_path / "projC" / "node_modules" / "x" / ".claude" / "skills" / "spar"
    pruned.mkdir(parents=True)
    pruned.joinpath("SKILL.md").write_text("PRUNED", encoding="utf-8")
    # grandparent traps: same-named dirs NOT owned by medulla must be left alone
    foreign_wf = tmp_path / "other" / "workflows" / "spar"           # no .medulla grandparent
    foreign_wf.mkdir(parents=True)
    foreign_wf.joinpath("workflow.yaml").write_text("FOREIGN", encoding="utf-8")
    foreign_sk = tmp_path / "other2" / "config" / "skills" / "spar"  # grandparent not .claude/.agents/.opencode
    foreign_sk.mkdir(parents=True)
    foreign_sk.joinpath("SKILL.md").write_text("FOREIGN", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["medulla", "refresh", "spar", str(tmp_path)])
    assert shim.entry() == 0
    assert wf.joinpath("workflow.yaml").read_text() != "STALE"        # workflow refreshed
    assert wf.joinpath("runs", "old", "keep").is_file()              # runs preserved
    assert sk.joinpath("SKILL.md").read_text() != "STALE"           # SKILL.md refreshed
    assert pruned.joinpath("SKILL.md").read_text() == "PRUNED"      # node_modules pruned
    assert foreign_wf.joinpath("workflow.yaml").read_text() == "FOREIGN"   # grandparent gate
    assert foreign_sk.joinpath("SKILL.md").read_text() == "FOREIGN"        # grandparent gate

    # --dry-run touches nothing
    sk.joinpath("SKILL.md").write_text("STALE2", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["medulla", "refresh", "spar", str(tmp_path), "--dry-run"])
    assert shim.entry() == 0
    assert sk.joinpath("SKILL.md").read_text() == "STALE2"          # dry-run: unchanged

    # no folder -> refuses with a clear error
    monkeypatch.setattr("sys.argv", ["medulla", "refresh", "spar"])
    assert shim.entry() == 1


def test_refresh_never_clobbers_through_symlink(tmp_path):
    # CWE-59: a booby-trapped deploy (a bundle-path file symlinked to a victim
    # OUTSIDE the deploy) must not be written through — the victim stays intact.
    from medulla.init import refresh_skill
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    skill_victim = tmp_path / "skill-victim.txt"
    skill_victim.write_text("medulla --apple -w SECRET", encoding="utf-8")
    wf = tmp_path / "proj" / ".medulla" / "workflows" / "spar"
    wf.mkdir(parents=True)
    (wf / "workflow.yaml").symlink_to(victim)        # deploy's workflow.yaml -> victim
    (wf / "SKILL.md").symlink_to(skill_victim)
    assert refresh_skill("spar", str(tmp_path)) == 0
    assert victim.read_text() == "SECRET"            # NOT clobbered
    assert (wf / "workflow.yaml").is_symlink()        # symlink left untouched
    assert skill_victim.read_text() == "medulla --apple -w SECRET"


def test_empty_local_workflow_does_not_shadow_the_shared_one(tmp_path, monkeypatch):
    # A zero-byte file appeared repeatedly and outranked ~/.medulla/workflows/<name>,
    # crashing every run in a way that looked like panelists failing to deliver.
    from medulla.v2.cli import _resolve_workflow_yaml
    home = tmp_path / "home"
    shared = home / ".medulla" / "workflows" / "spar"
    shared.mkdir(parents=True)
    (shared / "workflow.yaml").write_text('version: "2"\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    local = tmp_path / ".medulla" / "workflows" / "spar"
    local.mkdir(parents=True)
    (local / "workflow.yaml").write_text("", encoding="utf-8")        # debris
    assert _resolve_workflow_yaml(Path(".medulla/workflows/spar")) == shared / "workflow.yaml"

    (local / "workflow.yaml").write_text('version: "2"\n', encoding="utf-8")  # real intent
    got = _resolve_workflow_yaml(Path(".medulla/workflows/spar"))
    # returned as GIVEN (relative), so --print-run-dir stays valid for a caller on
    # the host while the run happened inside a container
    assert got == Path(".medulla/workflows/spar/workflow.yaml")
    assert got.resolve() == (local / "workflow.yaml").resolve()


def test_runs_under_env_overrides_the_runs_root(tmp_path, monkeypatch):
    # Under --docker a shared definition is mounted OUTSIDE /workspace on a read-only
    # path, so history cannot live beside it; docker.py points the engine at the project.
    from medulla.v2.rundir import runs_root_for
    monkeypatch.setenv("MEDULLA_RUNS_UNDER", "/workspace/.medulla/workflows/spar")
    assert runs_root_for(tmp_path / "anywhere") == Path("/workspace/.medulla/workflows/spar")
    monkeypatch.delenv("MEDULLA_RUNS_UNDER")
    assert runs_root_for(tmp_path / "anywhere") == tmp_path / "anywhere"
