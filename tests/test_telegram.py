import asyncio

from helpers import FakeTelegram, iso_ago

from jobradar.notify.telegram import LIMIT, Telegram, esc, format_channel, format_digest, pack


def job(i, title="Data <Engineer> & Co"):
    return {
        "uid": str(i),
        "title": title,
        "company": "Acme",
        "url": f"https://x/{i}",
        "via": "Himalayas" if i % 2 else None,
        "cities": ["Bengaluru"],
        "is_remote": False,
        "remote_scope": None,
        "skills": ["Python", "C++"],
        "first_seen": iso_ago(hours=1),
        "enrichment": {
            "summary": "Build pipelines",
            "experience_min": 0,
            "experience_max": 2,
            "fresher_friendly": True,
        },
    }


def test_escaping():
    assert esc("<b>&") == "&lt;b&gt;&amp;"


def test_digest_formats_and_escapes():
    items = [
        {
            "job": job(i),
            "score": 80 - i,
            "ai": {"verdict": "good", "tip": "Show your <SQL> work"},
            "matched": ["Python"],
            "missing": ["Kafka"],
        }
        for i in range(3)
    ]
    [msg] = format_digest(
        items, "https://me.github.io/jobradar/", {"new_jobs": 42, "open_jobs": 900}, "Asha Test"
    )
    assert "3 new matches for Asha" in msg
    assert "Data &lt;Engineer&gt; &amp; Co" in msg and "<Engineer>" not in msg
    assert "Match 80/100" in msg and "0-2 yrs" in msg and "Tip: Show your &lt;SQL&gt; work" in msg
    assert "via Himalayas" in msg and "42 new jobs today" in msg


def test_channel_post_has_hashtags():
    [msg] = format_channel([job(1)], "https://me.github.io/jobradar/")
    assert "#Bengaluru" in msg and "#Python" in msg and "#C" in msg and "#Fresher" in msg


def test_pack_respects_limit_without_splitting_blocks():
    blocks = [("x" * 900) + f" block{i}" for i in range(12)]
    msgs = pack("HEAD\n", blocks, "\nFOOT")
    assert len(msgs) > 1 and all(len(m) <= LIMIT for m in msgs)
    joined = "".join(msgs)
    assert all(f"block{i}" in joined for i in range(12)) and msgs[-1].endswith("FOOT")


def test_send_falls_back_to_plain_text_on_parse_error():
    fake = FakeTelegram(fail_parse_once=True)
    tg = Telegram("123:abc", transport=fake.transport())

    async def go():
        try:
            return await tg.send("42", "<b>Hello</b> &amp; bye")
        finally:
            await tg.aclose()

    assert asyncio.run(go()) is True
    assert fake.messages[0]["text"] == "Hello & bye" and "parse_mode" not in fake.messages[0]
