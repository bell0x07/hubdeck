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
    ("keys: {1: {colour: red}}", "colour"),
    ("{}", "nothing defined"),
    ("keys: {1: {action: discord.mute}}\nplugins: {discord: {}}", "client_id is required"),
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


def test_example_config_commented_sections_are_valid(write):
    """The commented-out dial/knobs/rollers/faders/lighting examples must work when uncommented."""
    import re
    from hubdeck.paths import EXAMPLE_CONFIG
    out, uncomment = [], False
    for line in EXAMPLE_CONFIG.read_text(encoding="utf-8").splitlines():
        if re.match(r"^# lighting:|^\s+# (dial|knobs|rollers|faders):", line):
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
    cfg = load_config(write("config.yaml", text))
    assert {"dial", "knobs", "rollers", "faders", "base"} <= cfg.used_modules()


def test_full_showcase_example_is_valid(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("HUBDECK_DISCORD_CLIENT_ID", "123")
    monkeypatch.setenv("HUBDECK_DISCORD_CLIENT_SECRET", "dummy")
    path = Path(__file__).resolve().parents[1] / "examples" / "config.yaml"
    cfg = load_config(path, secrets=path.parent / "no-secrets.yaml")
    assert cfg.used_modules() == {"keys", "dial", "knobs", "rollers", "faders", "base"}
    assert set(cfg.pages) == {"main", "media", "fun"}


def test_on_off_yes_no_stay_text(write):
    cfg = load_config(write("config.yaml", "keys: {1: {face: {label: ON, text: no, filled: true}}}"))
    face = cfg.pages["main"].keys[1].face
    assert (face.label, face.text, face.filled) == ("ON", "no", True)


def test_invalid_yaml(write):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write("config.yaml", "keys: {1: [unclosed"))
