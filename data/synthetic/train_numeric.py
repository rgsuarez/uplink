def add(a, b):
    return a + b

def clamp(n, low, high):
    return max(low, min(n, high))

def sign(n):
    return (n > 0) - (n < 0)

def safe_div(a, b):
    return a / b if b else None

def unique(values):
    return list(dict.fromkeys(values))

def invariant():
    return 7
