def identity(value):
    return value

def active_names(rows):
    return [r['name'] for r in rows if r.get('active', False)]

def total(rows):
    return sum(r['value'] for r in rows)

def lookup(row, key, default):
    return row.get(key, default)

def has_all(row, keys):
    return all(key in row for key in keys)

def counts(values):
    return {v: values.count(v) for v in set(values)}
