def identity(value):
    return value

def inclusive(n, low, high):
    return low <= n <= high

def midpoint(a, b):
    return (a + b) / 2

def distance(a, b):
    return abs(a - b)

def ceil_div(a, b):
    return (a + b - 1) // b

def discount(price, percent):
    return price * (100 - percent) / 100

def positive(n):
    return max(0, n)

def odd(n):
    return n % 2 == 1

def between(n, low, high):
    return low < n < high
