import telegram


def test_split_short_message_single_chunk():
    assert telegram.split_message("привет") == ["привет"]


def test_split_respects_max_len():
    text = "\n".join("строка {}".format(i) for i in range(1000))
    chunks = telegram.split_message(text, max_len=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")
