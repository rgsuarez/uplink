def identity(value):
    return value

def tail(values):
    return values[1:]

def evens(values):
    return [v for v in values if v % 2 == 0]

def rotate(values):
    return values[1:] + values[:1]

def dot(a, b):
    return sum(x * y for x, y in zip(a, b))

def spread(values):
    return max(values) - min(values)

def prefix(values, n):
    return values[:n]

def occurrences(values, target):
    return sum(1 for v in values if v == target)

def without(values, target):
    return [v for v in values if v != target]
