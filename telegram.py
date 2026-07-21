"""Отправка отчётов в Telegram ботом пользователя."""
import requests

MAX_LEN = 4000  # лимит API 4096, оставляем запас


def split_message(text, max_len=MAX_LEN):
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = current + "\n" + line if current else line
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            while len(line) > max_len:
                chunks.append(line[:max_len])
                line = line[max_len:]
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send(text, token, chat_id):
    for chunk in split_message(text):
        resp = requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(token),
            json={"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True},
            timeout=30,
        )
        resp.raise_for_status()
