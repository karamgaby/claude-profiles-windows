"""Claude Code in WSL2: detection, the wsl.exe argv, and the lazy client.

Nothing here touches a real distro: a scripted runner stands in for wsl.exe.
The real path was checked by hand against Ubuntu on Windows 11 (see the
commit that added this file).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from claude_profiles.services import wsl
from claude_profiles.services.cswap_client import (
    CswapClient,
    CswapError,
    CswapErrorKind,
)
from claude_profiles.services.settings_service import Settings

HOME = "/home/dev"
BUNDLED = f"{HOME}/.local/share/claude-profiles/cswap-0.26.0/cswap/cswap"
TARBALL = Path(r"C:\Apps\Claude Profiles\cswap-linux-0.26.0.tar.gz")


@dataclass
class Done:
    returncode: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


class FakeWsl:
    """Answers wsl.exe calls by what follows ``--exec`` (or the list flags)."""

    def __init__(
        self,
        *,
        distros: str = "Ubuntu\r\ndocker-desktop\r\n",
        running: str | None = "Ubuntu\r\n",
        home: str = HOME,
        which: str = f"{HOME}/.local/bin/cswap\n{HOME}/.local/bin/claude\n",
        version: str = "cswap 0.29.0\n",
        utf16: bool = False,
        vscode: str = "",
        unpacked: str | None = None,
    ) -> None:
        self.distros = distros
        self.running = running
        self.home = home
        self.which = which
        self.version = version
        self.utf16 = utf16
        self.vscode = vscode  # what find lists under ~/.vscode-server
        self.unpacked = unpacked  # what an unpacked bundled cswap reports, if there is one
        self.calls: list[list[str]] = []

    def _out(self, text: str, *, wsl_message: bool = False) -> bytes:
        # wsl.exe's own listing is UTF-16LE when WSL_UTF8 is ignored.
        if wsl_message and self.utf16:
            return text.encode("utf-16-le")
        return text.encode("utf-8")

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert kwargs["env"]["WSL_UTF8"] == "1"
        assert "shell" not in kwargs
        if argv[1:] == ["-l", "-q"]:
            return Done(stdout=self._out(self.distros, wsl_message=True))
        if argv[1:] == ["-l", "--running", "-q"]:
            if self.running is None:
                return Done(returncode=1, stdout=self._out("no running", wsl_message=True))
            return Done(stdout=self._out(self.running, wsl_message=True))
        command = argv[argv.index("--exec") + 1 :]
        if command == ["printenv", "HOME"]:
            return Done(stdout=self._out(self.home + "\n"))
        if command[0] == "/usr/bin/env" and command[2:] == ["which", "cswap", "claude"]:
            return Done(returncode=0 if "claude" in self.which else 1, stdout=self._out(self.which))
        if command[0] == "/usr/bin/env" and command[-1] == "--version":
            return Done(stdout=self._out(self.version))
        if command[0] == "/usr/bin/env" and command[2] == "find":
            return Done(stdout=self._out(self.vscode))
        if command == [BUNDLED, "--version"]:
            if self.unpacked is None:
                return Done(returncode=127, stderr=self._out("No such file or directory"))
            return Done(stdout=self._out(self.unpacked))
        if command[0] == "wslpath":
            return Done(stdout=self._out("/mnt/c/Apps/Claude Profiles/cswap-linux-0.26.0.tar.gz\n"))
        if command[0] == "mkdir":
            return Done()
        if command[0] == "tar":
            self.unpacked = "cswap 0.26.0\n"
            return Done()
        raise AssertionError(f"unexpected wsl call: {argv}")


# --- listing ---------------------------------------------------------------


@pytest.mark.parametrize("utf16", [False, True])
def test_distros_are_listed_in_either_encoding(utf16):
    fake = FakeWsl(distros="Ubuntu-24.04\r\nDebian\r\ndocker-desktop\r\n", utf16=utf16)
    assert wsl.list_distros(fake) == ["Ubuntu-24.04", "Debian"]


def test_listing_never_starts_a_distro():
    fake = FakeWsl()
    wsl.list_distros(fake)
    wsl.running_distros(fake)
    assert all("--exec" not in call and "-d" not in call for call in fake.calls)


def test_odd_names_are_not_offered():
    fake = FakeWsl(distros="Ubuntu\r\nhas space\r\n-rf\r\n$(boom)\r\n")
    assert wsl.list_distros(fake) == ["Ubuntu"]


def test_no_wsl_means_no_distros():
    def missing(argv, **kwargs):
        raise FileNotFoundError("wsl.exe")

    assert wsl.list_distros(missing) == []
    assert wsl.running_distros(missing) is None


def test_nothing_running_is_an_empty_set():
    assert wsl.running_distros(FakeWsl(running=None)) == set()


def test_wsl_exe_comes_from_system_root_not_path(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"D:\Win")
    assert wsl.wsl_executable() == r"D:\Win\System32\wsl.exe"


# --- detection ---------------------------------------------------------------


def test_detection_finds_cswap_claude_and_version():
    env = wsl.detect("Ubuntu", FakeWsl())
    assert env.home == HOME
    assert env.cswap == f"{HOME}/.local/bin/cswap"
    assert env.claude == f"{HOME}/.local/bin/claude"
    assert env.version == (0, 29, 0)


def test_missing_cswap_says_how_to_install_it():
    with pytest.raises(wsl.WslProblem, match="uv tool install claude-swap"):
        wsl.detect("Ubuntu", FakeWsl(which=""))


def test_an_old_cswap_says_how_to_upgrade_it():
    with pytest.raises(wsl.WslProblem, match="uv tool upgrade claude-swap"):
        wsl.detect("Ubuntu", FakeWsl(version="cswap 0.25.9"))


def test_a_missing_distro_is_named():
    with pytest.raises(wsl.WslProblem, match="no WSL distribution called Arch"):
        wsl.detect("Arch", FakeWsl())


def test_an_unusable_home_is_refused():
    with pytest.raises(wsl.WslProblem, match="could not be started"):
        wsl.detect("Ubuntu", FakeWsl(home="relative/home"))


def test_claude_is_optional_for_detection():
    env = wsl.detect("Ubuntu", FakeWsl(which=f"{HOME}/.local/bin/cswap\n"))
    assert env.claude is None
    assert env.sign_in_command() is None


def test_a_boot_timeout_is_explained():
    fake = FakeWsl()

    def slow(argv, **kwargs):
        if "--exec" in argv:
            raise subprocess.TimeoutExpired(argv, 45)
        return fake(argv, **kwargs)

    with pytest.raises(wsl.WslProblem, match="did not start in time"):
        wsl.detect("Ubuntu", slow)


# --- the argv ----------------------------------------------------------------


def test_every_wsl_call_is_exec_without_a_shell():
    env = wsl.detect("Ubuntu", FakeWsl())
    prefix = env.cswap_prefix()
    assert prefix[0] == wsl.wsl_executable()
    assert prefix[1:3] == ["-d", "Ubuntu"]
    assert prefix[prefix.index("--exec") + 1] == "/usr/bin/env"
    assert prefix[-1] == env.cswap
    for word in prefix:
        assert word not in {"sh", "bash", "-c", "--shell-type"}


def test_cswap_and_claude_find_user_installs():
    """--exec skips the login shell, so ~/.local/bin must be put on PATH."""
    env = wsl.detect("Ubuntu", FakeWsl())
    path = next(w for w in env.cswap_prefix() if w.startswith("PATH="))
    assert path.startswith(f"PATH={HOME}/.local/bin:")
    assert "/usr/bin" in path


def test_run_and_sign_in_commands():
    env = wsl.detect("Ubuntu", FakeWsl())
    client = CswapClient(argv_prefix=env.cswap_prefix())
    assert client.build_run_command(2)[-3:] == [env.cswap, "run", "2"]
    assert env.sign_in_command()[-3:] == [env.claude, "auth", "login"]


def test_the_allowlist_still_applies_behind_the_prefix():
    calls = []
    client = CswapClient(argv_prefix=["wsl.exe", "cswap"], runner=lambda *a, **k: calls.append(a))
    with pytest.raises(CswapError) as excinfo:
        client._invoke(["remove", "1"], 5)
    assert excinfo.value.kind is CswapErrorKind.DISALLOWED
    assert calls == []


def test_a_launcher_failure_is_not_installed_not_garbage():
    """wsl.exe failing prints no JSON; that must not count toward backoff."""

    def failing(argv, **kwargs):
        return Done(returncode=4294967295, stdout="The distribution could not start.")

    client = CswapClient(
        argv_prefix=["wsl.exe", "cswap"], runner=failing, unreadable_message="nope"
    )
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.NOT_INSTALLED
    assert not excinfo.value.kind.is_transient


# --- the lazy client ---------------------------------------------------------


def test_detection_runs_once_and_is_reused():
    fake = FakeWsl()
    client = wsl.WslCswapClient("Ubuntu", runner=fake)
    client.ensure_detected()
    before = len(fake.calls)
    client.ensure_detected()
    assert len(fake.calls) == before


def test_a_failed_detection_is_retried_next_time():
    """Installing claude-swap in the distro is picked up without a restart."""
    fake = FakeWsl(which="")
    client = wsl.WslCswapClient("Ubuntu", runner=fake)
    with pytest.raises(CswapError) as excinfo:
        client.ensure_detected()
    assert excinfo.value.kind is CswapErrorKind.NOT_INSTALLED

    fake.which = f"{HOME}/.local/bin/cswap\n"
    assert client.ensure_detected().cswap == f"{HOME}/.local/bin/cswap"


def test_a_stopped_distro_is_dormant_and_stays_stopped():
    fake = FakeWsl(running="Debian\r\n")
    client = wsl.WslCswapClient("Ubuntu", runner=fake)
    assert client.dormant_reason() == "Ubuntu is not running"
    assert all("--exec" not in call for call in fake.calls)


def test_a_running_distro_may_be_polled():
    assert wsl.WslCswapClient("Ubuntu", runner=FakeWsl()).dormant_reason() is None


def test_description_mentions_a_missing_claude():
    fake = FakeWsl(which=f"{HOME}/.local/bin/cswap\n")
    text = wsl.WslCswapClient("Ubuntu", runner=fake).describe()
    assert "claude-swap 0.29.0 in Ubuntu" in text
    assert "install.sh" in text


# --- the setting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("windows", "windows"),
        ("wsl:Ubuntu-24.04", "wsl:Ubuntu-24.04"),
        ("wsl:", "windows"),
        ("wsl:bad name", "windows"),
        ("linux", "windows"),
        (42, "windows"),
    ],
)
def test_the_environment_setting_is_normalized(stored, expected):
    assert Settings(claude_environment=stored).normalized().claude_environment == expected


def test_labels():
    assert wsl.environment_label("windows") == "Windows"
    assert wsl.environment_label("wsl:Ubuntu") == "Ubuntu (WSL)"


@pytest.mark.parametrize(
    ("primary", "stored", "expected"),
    [
        ("windows", "wsl:Ubuntu", "wsl:Ubuntu"),
        ("wsl:Ubuntu", "windows", "windows"),
        ("windows", "windows", ""),  # never the place Claude Code already runs in
        ("wsl:Ubuntu", "wsl:Ubuntu", ""),
        ("windows", "wsl:bad name", ""),
        ("windows", 42, ""),
    ],
)
def test_the_mirror_setting_is_normalized(primary, stored, expected):
    settings = Settings(claude_environment=primary, mirror_environment=stored)
    assert settings.normalized().mirror_environment == expected


# --- nothing to install in the distro ----------------------------------------------


@pytest.fixture
def bundled(monkeypatch):
    monkeypatch.setattr(wsl, "bundled_linux_cswap", lambda: (TARBALL, (0, 26, 0)))


def _commands(fake: FakeWsl) -> list[str]:
    return [call[call.index("--exec") + 1] for call in fake.calls if "--exec" in call]


def test_the_shipped_cswap_is_unpacked_into_a_distro_without_one(bundled):
    fake = FakeWsl(which=f"{HOME}/.local/bin/claude")

    env = wsl.detect("Ubuntu", fake)

    assert env.cswap == BUNDLED and env.version == (0, 26, 0)
    wslpath = next(c for c in fake.calls if "wslpath" in c)
    assert wslpath[-1] == str(TARBALL)  # one argv entry, spaces and all
    tar = next(c for c in fake.calls if "tar" in c)
    assert tar[-4:] == [
        "-xzf",
        "/mnt/c/Apps/Claude Profiles/cswap-linux-0.26.0.tar.gz",
        "-C",
        f"{HOME}/.local/share/claude-profiles/cswap-0.26.0",
    ]


def test_an_unpacked_copy_is_reused(bundled):
    fake = FakeWsl(unpacked="cswap 0.26.0")
    assert wsl.detect("Ubuntu", fake).cswap == BUNDLED
    assert "tar" not in _commands(fake) and "wslpath" not in _commands(fake)


def test_a_copy_from_another_version_is_replaced(bundled):
    fake = FakeWsl(unpacked="cswap 0.25.0")
    wsl.detect("Ubuntu", fake)
    assert "tar" in _commands(fake)


def test_vscodes_own_claude_signs_in_when_none_is_installed():
    ext = f"{HOME}/.vscode-server/extensions"
    old = f"{ext}/anthropic.claude-code-2.1.99-linux-x64/resources/native-binary/claude"
    new = f"{ext}/anthropic.claude-code-2.1.319-linux-x64/resources/native-binary/claude"
    fake = FakeWsl(which=f"{HOME}/.local/bin/cswap", vscode=f"{old}\n{new}\n")

    env = wsl.detect("Ubuntu", fake)

    assert env.claude == new  # the newest, compared as versions rather than text
    assert env.sign_in_command()[-3:] == [new, "auth", "login"]
    # cswap run looks claude up on PATH, so its folder is added.
    assert env.exec_prefix()[-1].endswith(":" + new.rsplit("/", 1)[0])


def test_the_shipped_tarball_is_found_beside_the_installed_app(tmp_path, monkeypatch):
    exe = tmp_path / "ClaudeProfiles.exe"
    exe.write_bytes(b"")
    (tmp_path / "cswap-linux-0.26.0.tar.gz").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(exe))

    assert wsl.bundled_linux_cswap() is None  # a source checkout ships none
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert wsl.bundled_linux_cswap() == (tmp_path / "cswap-linux-0.26.0.tar.gz", (0, 26, 0))
