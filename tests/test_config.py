import pytest

from hubdeck.config import ConfigError, load_config
from hubdeck.config.schema import Action


def test_single_page_shorthand_and_actions(write):
    p = write("config.yaml", """
        keys:
          1:
            action: {keys: "ctrl+c"}
            on_long_press: [rec.a, {rec.b: 3}]
            face: {icon: copy, label: Copy}
    """)
    cfg = load_config(p)
    kd = cfg.pages["main"].keys[1]
    assert kd.on_press == [Action(plugin="keyboard", name="keys", arg="ctrl+c")]
    assert kd.on_long_press == [Action(plugin="rec", name="a"), Action(plugin="rec", name="b", arg=3)]
    assert cfg.used_plugins() == {"keyboard", "rec"}


def test_python_action(write):
    cfg = load_config(write("config.yaml", """
        keys:
          1:
            action:
              python: |
                import os
                log.info(os.getpid())
    """))
    assert cfg.pages["main"].keys[1].on_press == [
        Action(plugin="python", name="exec", arg="import os\nlog.info(os.getpid())\n")]


def test_secrets_from_file_and_env(write, monkeypatch):
    write("secrets.yaml", "discord_client_secret: s3cret\n")
    p = write("config.yaml", """
        plugins:
          discord: {client_id: "1", client_secret: !secret discord_client_secret}
          rec: {token: !secret other}
        keys: {1: {action: discord.toggle_mute}}
    """)
    monkeypatch.setenv("HUBDECK_OTHER", "from-env")
    cfg = load_config(p)
    assert cfg.plugins["discord"]["client_secret"] == "s3cret"
    assert cfg.plugins["rec"]["token"] == "from-env"


def test_missing_secret(write):
    p = write("config.yaml", """
        plugins: {discord: {client_id: "1", client_secret: !secret nope}}
        keys: {1: {action: discord.mute}}
    """)
    with pytest.raises(ConfigError, match="nope"):
        load_config(p)


@pytest.mark.parametrize("body, message", [
    ("keys: {16: {action: rec.a}}", "1-15"),
    ("keys: {1: {action: rec.nope}}", "no action 'nope'"),
    ("keys: {1: {action: nothing.x}}", "unknown plugin 'nothing'"),
    ("keys: {1: {action: {page: missing}}}", "no page named 'missing'"),
    ("keys: {1: {face: {icon: microphon}}}", "did you mean microphone"),
    ("keys: {1: {face: {bg: notacolour}}}", "unknown colour"),
    ("keys: {1: {action: {keys: 'ctrl+banana'}}}", "unknown key 'banana'"),
    ("keys: {1: {face: {label: '{rec.missing}'}}}", "no state 'missing'"),
    ("keys: {1: {face_on: {bg: red}}}", "need a 'state'"),
    ("keys: {1: {state: {rec.missing: x}, face_on: {bg: red}}}", "no state 'missing'"),
    ("keys: {1: {state: {rec.on: x, rec.value: y}}}", "one plugin.state"),
    ("keys: {1: {state: {rec.value: [x]}}}", "text, number or true/false"),
    ("lighting: {base: {state: {Rec.value: x}, on: {color: red}}}\nkeys: {1: {action: rec.a}}", "must look like plugin.state"),
    ("keys: {1: {colour: red}}", "colour"),
    ("{}", "nothing defined"),
    ("keys: {1: {action: discord.mute}}\nplugins: {discord: {}}", "client_id is required"),
    ("knobs: {1: {action: rec.a, on_turn: rec.b}}", "on_turn runs after a turn"),
    ("knobs: {1: {turn: spotify.volume}}", "spotify.client_id is required"),      # no plugins section
    ("knobs: {1: {turn: {target: spotify.volume, arg: x}}}\nplugins: {spotify: {client_id: c}}", "takes no arg"),
    ("keys: {1: {action: spotify.volume_up}}\nplugins: {spotify: {client_id: c, redirect_uri: 'http://localhost:1/x'}}",
     "doesn't allow localhost"),
    ("keys: {1: {action: {python: 'x ='}}}", r"python: .*\(line 1\)"),
    ("keys: {1: {action: {python: 5}}}", "needs code"),
])
def test_validation_errors(write, body, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write("config.yaml", body))


def test_example_secrets_placeholder_is_rejected(write):
    from hubdeck.paths import EXAMPLE_SECRETS
    write("secrets.yaml", EXAMPLE_SECRETS.read_text(encoding="utf-8"))
    p = write("config.yaml", """
        plugins: {discord: {client_id: "1", client_secret: !secret discord_client_secret}}
        keys: {1: {action: discord.mute}}
    """)
    with pytest.raises(ConfigError, match="still the placeholder"):
        load_config(p)


def test_example_config_is_valid():
    from hubdeck.paths import EXAMPLE_CONFIG
    cfg = load_config(EXAMPLE_CONFIG)
    assert set(cfg.pages) == {"main", "media"}


def test_example_config_commented_sections_are_valid(write, monkeypatch):
    """The commented-out plugins/dial/knobs/rollers/faders/lighting/match examples, and the Discord
    and Home Assistant keys, must work when uncommented."""
    import re
    from hubdeck.paths import EXAMPLE_CONFIG
    out, uncomment = [], False
    for line in EXAMPLE_CONFIG.read_text(encoding="utf-8").splitlines():
        if re.match(r"^# (lighting|plugins):|^\s+# (dial|knobs|rollers|faders|match|11|13):", line):
            uncomment = True
        elif uncomment and not re.match(r"^\s*#( |$)", line):
            uncomment = False
        if uncomment and not re.match(r"^\s*#\s*$", line):
            line = re.sub(r"^(\s*)# ", r"\1", line, count=1)
        elif uncomment:
            line = ""
        out.append(line)
    text = "\n".join(out)
    assert "\n    dial:" in text and "\nlighting:" in text
    for name in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "SPOTIFY_CLIENT_ID", "HA_TOKEN"):
        monkeypatch.setenv(f"HUBDECK_{name}", "dummy")
    cfg = load_config(write("config.yaml", text))
    assert {"dial", "knobs", "rollers", "faders", "base"} <= cfg.used_modules()
    assert {"discord", "spotify", "ha"} <= cfg.used_plugins()
    assert ("ha", "on[light.desk]") in cfg.state_refs()
    assert cfg.pages["media"].match[0].app == "spotify.exe"


def test_full_showcase_example_is_valid(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("HUBDECK_DISCORD_CLIENT_ID", "123")
    monkeypatch.setenv("HUBDECK_DISCORD_CLIENT_SECRET", "dummy")
    monkeypatch.setenv("HUBDECK_SPOTIFY_CLIENT_ID", "dummy")
    monkeypatch.setenv("HUBDECK_HA_TOKEN", "dummy")
    path = Path(__file__).resolve().parents[1] / "examples" / "config.yaml"
    cfg = load_config(path, secrets=path.parent / "no-secrets.yaml")
    assert cfg.used_modules() == {"keys", "dial", "knobs", "rollers", "faders", "base"}
    assert set(cfg.pages) == {"main", "media", "call", "fun", "house"}
    assert cfg.pages["media"].match and cfg.auto_switch.ignore and cfg.auto_switch.on_focus
    assert cfg.pages["call"].sticky and cfg.pages["call"].match[0].rank == 2   # app + title


def test_on_off_yes_no_stay_text(write):
    cfg = load_config(write("config.yaml", "keys: {1: {face: {label: ON, text: no, filled: true}}}"))
    face = cfg.pages["main"].keys[1].face
    assert (face.label, face.text, face.filled) == ("ON", "no", True)


def test_invalid_yaml(write):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write("config.yaml", "keys: {1: [unclosed"))


def test_home_after(write):
    cfg = load_config(write("config.yaml", """
pages:
  main: {keys: {1: {face: {label: M}}}}
  a: {home_after: 60}
  b: {home_after: {idle: 60, press: 15}}
  c: {home_after: {press: 15}}
"""))
    a, b, c = (cfg.pages[p].home_after for p in "abc")
    assert (a.seconds(pressed=False), a.seconds(pressed=True)) == (60, 60)
    assert (b.seconds(pressed=False), b.seconds(pressed=True)) == (60, 15)
    assert (c.seconds(pressed=False), c.seconds(pressed=True)) == (15, 15)
    for pages, message in [("{main: {home_after: 60}}", "is the start page"),
                           ("{main: {}, a: {home_after: {}}}", "needs 'idle', 'press' or both"),
                           ("{main: {}, a: {home_after: 0}}", "greater than 0")]:
        with pytest.raises(ConfigError, match=message):
            load_config(write("config.yaml", f"pages: {pages}"))
