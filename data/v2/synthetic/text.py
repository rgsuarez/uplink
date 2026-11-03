def identity(value):
    return value

def initials(text):
    return ''.join(word[0].upper() for word in text.split())

def unquote(text):
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text

def extension(path):
    return path.rsplit('.', 1)[1] if '.' in path else ''

def contains_word(text, word):
    return word in text.split()

def normalize(text):
    return ' '.join(text.lower().split())
