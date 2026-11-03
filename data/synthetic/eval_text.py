def join_words(words):
    return " ".join(words)

def suffix(text, count):
    return text[-count:] if count else ""

def is_blank(text):
    return not text.strip()

def invariant():
    return 11
