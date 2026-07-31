"""Markdown → WhatsApp conversion. WhatsApp renders single-asterisk bold, underscore
italic, single-tilde strike, backtick code, lists and quotes — NOT headings, double
markers, links or tables. `to_whatsapp` rewrites what maps and neutralises what doesn't."""

from coworker.connectors.wa_format import to_whatsapp


def test_headings_become_bold_lines():
    assert to_whatsapp("### Aguardando sua aprovação:") == "*Aguardando sua aprovação:*"
    assert to_whatsapp("# Top\nbody\n###### Deep") == "*Top*\nbody\n*Deep*"
    assert to_whatsapp("## Closed ##") == "*Closed*"


def test_double_markers_become_single():
    assert to_whatsapp("**bold** and __also bold__") == "*bold* and *also bold*"
    assert to_whatsapp("~~gone~~") == "~gone~"
    assert to_whatsapp("***both***") == "*_both_*"


def test_links_flatten_to_text_and_url():
    assert to_whatsapp("[docs](https://x.dev/a)") == "docs (https://x.dev/a)"
    assert (
        to_whatsapp("[https://x.dev/a](https://x.dev/a)") == "https://x.dev/a"
    ), "self-link keeps just the url"
    assert to_whatsapp("![diagram](https://x.dev/i.png)") == "https://x.dev/i.png"


def test_horizontal_rules_are_dropped():
    assert to_whatsapp("above\n---\nbelow") == "above\nbelow"
    assert to_whatsapp("a\n***\nb\n___\nc") == "a\nb\nc"


def test_dashes_with_content_are_not_a_rule():
    assert to_whatsapp("--- keep this line") == "--- keep this line"


def test_lists_and_quotes_pass_through():
    text = "- item\n* other\n1. first\n> quoted"
    assert to_whatsapp(text) == text


def test_code_spans_are_untouched():
    fenced = "```\n### not a heading\n**not bold**\n```"
    assert to_whatsapp(fenced) == fenced
    assert to_whatsapp("run `git log --format='**'` now") == "run `git log --format='**'` now"


def test_tables_become_monospace_blocks():
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert to_whatsapp(table) == "```\n| a | b |\n| 1 | 2 |\n```"


def test_idempotent_on_whatsapp_native_text():
    native = "*bold* _it_ ~s~ `code`\n- item\n> quote"
    assert to_whatsapp(native) == native
    assert to_whatsapp(to_whatsapp("### H\n**b** [t](https://u.dev)")) == to_whatsapp(
        "### H\n**b** [t](https://u.dev)"
    )


def test_real_world_session_listing():
    raw = (
        "Você tem *6 sessões ativas*:\n\n"
        "### Aguardando sua aprovação (waiting_approval):\n"
        "1. *nexttrace* – TTY: ttys000\n"
    )
    got = to_whatsapp(raw)
    assert "###" not in got
    assert "*Aguardando sua aprovação (waiting_approval):*" in got


def test_empty_and_plain_text():
    assert to_whatsapp("") == ""
    assert to_whatsapp("oi, tudo bem?") == "oi, tudo bem?"
