"""Human-owned behavior oracles. Never derive expected values from candidate code."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilySpec:
    name: str
    split: str
    source: str
    cases: dict[str, tuple[tuple[str, str], ...]]
    regression: tuple[tuple[str, str], ...]


def specs() -> tuple[FamilySpec, ...]:
    return (
        FamilySpec(
            "inflection",
            "train",
            "data/upstream/inflection/module.py",
            {
                "camelize": (
                    ("s.camelize('red_green')", "'RedGreen'"),
                    ("s.camelize('red_green',False)", "'redGreen'"),
                    ("s.camelize('')", "''"),
                ),
                "dasherize": (
                    ("s.dasherize('a_b_c')", "'a-b-c'"),
                    ("s.dasherize('abc')", "'abc'"),
                    ("s.dasherize('')", "''"),
                ),
                "humanize": (
                    ("s.humanize('author_id')", "'Author'"),
                    ("s.humanize('employee_salary')", "'Employee salary'"),
                    ("s.humanize('')", "''"),
                ),
                "ordinal": (
                    ("s.ordinal(1)", "'st'"),
                    ("s.ordinal(2)", "'nd'"),
                    ("s.ordinal(3)", "'rd'"),
                    ("s.ordinal(11)", "'th'"),
                    ("s.ordinal(12)", "'th'"),
                    ("s.ordinal(-23)", "'rd'"),
                ),
                "ordinalize": (
                    ("s.ordinalize(1)", "'1st'"),
                    ("s.ordinalize(-12)", "'-12th'"),
                    ("s.ordinalize(33)", "'33rd'"),
                ),
                "parameterize": (
                    ("s.parameterize('Donald E. Knuth')", "'donald-e-knuth'"),
                    ("s.parameterize('red green', '_')", "'red_green'"),
                    ("s.parameterize('')", "''"),
                ),
                "pluralize": (
                    ("s.pluralize('post')", "'posts'"),
                    ("s.pluralize('child')", "'children'"),
                    ("s.pluralize('sheep')", "'sheep'"),
                    ("s.pluralize('')", "''"),
                ),
                "singularize": (
                    ("s.singularize('posts')", "'post'"),
                    ("s.singularize('children')", "'child'"),
                    ("s.singularize('sheep')", "'sheep'"),
                ),
                "tableize": (
                    ("s.tableize('FancyCategory')", "'fancy_categories'"),
                    ("s.tableize('RawScaledScorer')", "'raw_scaled_scorers'"),
                    ("s.tableize('sheep')", "'sheep'"),
                ),
                "titleize": (
                    ("s.titleize('man from the boondocks')", "'Man From The Boondocks'"),
                    ("s.titleize('TheManWithoutAPast')", "'The Man Without A Past'"),
                ),
                "transliterate": (
                    ("s.transliterate('älämölö')", "'alamolo'"),
                    ("s.transliterate('plain')", "'plain'"),
                    ("s.transliterate('')", "''"),
                ),
                "underscore": (
                    ("s.underscore('DeviceType')", "'device_type'"),
                    ("s.underscore('IOError')", "'io_error'"),
                    ("s.underscore('red-green')", "'red_green'"),
                ),
            },
            (("s.dasherize('plain')", "'plain'"), ("s.ordinal(4)", "'th'")),
        ),
        FamilySpec(
            "boltons",
            "train",
            "data/v2/upstream/boltons/module.py",
            {
                "clamp": (
                    ("s.clamp(2,0,4)", "2"),
                    ("s.clamp(-1,0,4)", "0"),
                    ("s.clamp(9,0,4)", "4"),
                ),
                "ceil": (
                    ("s.ceil(2.1)", "3"),
                    ("s.ceil(3,[1,3,5])", "3"),
                    ("s.ceil(2,[1,3,5])", "3"),
                ),
                "floor": (
                    ("s.floor(2.9)", "2"),
                    ("s.floor(3,[1,3,5])", "3"),
                    ("s.floor(4,[1,3,5])", "3"),
                ),
                "Bits.__init__": (
                    ("s.Bits('101').as_int()", "5"),
                    ("s.Bits('0x0F').as_bin()", "'00001111'"),
                    ("s.Bits('',0).as_bin()", "''"),
                ),
                "Bits.__getitem__": (
                    ("s.Bits('101')[0]", "True"),
                    ("s.Bits('101')[1]", "False"),
                    ("s.Bits('101')[-1]", "True"),
                    ("s.Bits('101')[1:].as_bin()", "'01'"),
                ),
                "Bits.__len__": (("len(s.Bits('101'))", "3"), ("len(s.Bits(''))", "0")),
                "Bits.__eq__": (
                    ("s.Bits('101') == s.Bits('101')", "True"),
                    ("s.Bits('101') == s.Bits('100')", "False"),
                    ("s.Bits('1') == s.Bits('01')", "False"),
                ),
                "Bits.__or__": (
                    ("(s.Bits('101') | s.Bits('010')).as_bin()", "'111'"),
                    ("(s.Bits('01') | s.Bits('11')).as_int()", "3"),
                ),
                "Bits.__and__": (
                    ("(s.Bits('101') & s.Bits('110')).as_bin()", "'100'"),
                    ("(s.Bits('00') & s.Bits('11')).as_int()", "0"),
                ),
                "Bits.__lshift__": (
                    ("(s.Bits('101') << 1).as_bin()", "'1010'"),
                    ("(s.Bits('1') << 0).as_bin()", "'1'"),
                ),
                "Bits.__rshift__": (
                    ("(s.Bits('101') >> 1).as_bin()", "'10'"),
                    ("(s.Bits('101') >> 8).as_bin()", "''"),
                ),
                "Bits.as_list": (
                    ("s.Bits('101').as_list()", "[True,False,True]"),
                    ("s.Bits('').as_list()", "[]"),
                ),
                "Bits.as_bin": (("s.Bits(5,4).as_bin()", "'0101'"), ("s.Bits('').as_bin()", "''")),
                "Bits.as_hex": (
                    ("s.Bits('00001111').as_hex()", "'0F'"),
                    ("s.Bits('').as_hex()", "''"),
                ),
                "Bits.as_int": (("s.Bits('101').as_int()", "5"), ("s.Bits('').as_int()", "0")),
                "Bits.as_bytes": (
                    ("s.Bits('00001111').as_bytes()", "b'\\x0f'"),
                    ("s.Bits('').as_bytes()", "b''"),
                ),
                "Bits.from_list": (
                    ("s.Bits.from_list([True,False]).as_bin()", "'10'"),
                    ("s.Bits.from_list([]).as_int()", "0"),
                ),
                "Bits.from_bin": (
                    ("s.Bits.from_bin('101').as_int()", "5"),
                    ("s.Bits.from_bin('').as_int()", "0"),
                ),
                "Bits.from_hex": (
                    ("s.Bits.from_hex('0F').as_int()", "15"),
                    ("s.Bits.from_hex('').as_int()", "0"),
                ),
                "Bits.from_int": (
                    ("s.Bits.from_int(5,4).as_bin()", "'0101'"),
                    ("s.Bits.from_int(0).as_int()", "0"),
                ),
                "Bits.from_bytes": (
                    ("s.Bits.from_bytes(b'\\x0f').as_bin()", "'00001111'"),
                    ("s.Bits.from_bytes(b'').as_int()", "0"),
                ),
            },
            (("s.clamp(1,0,5)", "1"), ("s.ceil(4,[2,4,8])", "4")),
        ),
        FamilySpec(
            "stringcase",
            "heldout",
            "data/upstream/stringcase/module.py",
            {
                "camelcase": (
                    ("s.camelcase('red_green')", "'redGreen'"),
                    ("s.camelcase('red-green')", "'redGreen'"),
                    ("s.camelcase('')", "''"),
                ),
                "capitalcase": (
                    ("s.capitalcase('red')", "'Red'"),
                    ("s.capitalcase('rED')", "'RED'"),
                    ("s.capitalcase('')", "''"),
                ),
                "constcase": (
                    ("s.constcase('redGreen')", "'RED_GREEN'"),
                    ("s.constcase('red_green')", "'RED_GREEN'"),
                    ("s.constcase('')", "''"),
                ),
                "lowercase": (
                    ("s.lowercase('AbC')", "'abc'"),
                    ("s.lowercase(123)", "'123'"),
                    ("s.lowercase('')", "''"),
                ),
                "pascalcase": (
                    ("s.pascalcase('red_green')", "'RedGreen'"),
                    ("s.pascalcase('red')", "'Red'"),
                    ("s.pascalcase('')", "''"),
                ),
                "pathcase": (
                    ("s.pathcase('redGreen')", "'red/green'"),
                    ("s.pathcase('red')", "'red'"),
                    ("s.pathcase('')", "''"),
                ),
                "backslashcase": (
                    ("s.backslashcase('redGreen')", "'red\\\\green'"),
                    ("s.backslashcase('red')", "'red'"),
                    ("s.backslashcase('')", "''"),
                ),
                "sentencecase": (
                    ("s.sentencecase('redGreen')", "'Red green'"),
                    ("s.sentencecase('red_green')", "'Red green'"),
                    ("s.sentencecase('')", "''"),
                ),
                "snakecase": (
                    ("s.snakecase('redGreen')", "'red_green'"),
                    ("s.snakecase('red green')", "'red_green'"),
                    ("s.snakecase('')", "''"),
                ),
                "spinalcase": (
                    ("s.spinalcase('redGreen')", "'red-green'"),
                    ("s.spinalcase('red')", "'red'"),
                    ("s.spinalcase('')", "''"),
                ),
                "dotcase": (
                    ("s.dotcase('redGreen')", "'red.green'"),
                    ("s.dotcase('red')", "'red'"),
                    ("s.dotcase('')", "''"),
                ),
                "titlecase": (
                    ("s.titlecase('redGreen')", "'Red Green'"),
                    ("s.titlecase('red_green')", "'Red Green'"),
                    ("s.titlecase('')", "''"),
                ),
                "trimcase": (
                    ("s.trimcase(' red ')", "'red'"),
                    ("s.trimcase('red')", "'red'"),
                    ("s.trimcase(' ')", "''"),
                ),
                "uppercase": (
                    ("s.uppercase('AbC')", "'ABC'"),
                    ("s.uppercase(123)", "'123'"),
                    ("s.uppercase('')", "''"),
                ),
                "alphanumcase": (
                    ("s.alphanumcase('a!b@2')", "'ab2'"),
                    ("s.alphanumcase('abc')", "'abc'"),
                    ("s.alphanumcase('')", "''"),
                ),
            },
            (("s.trimcase('plain')", "'plain'"), ("s.lowercase('123')", "'123'")),
        ),
        FamilySpec(
            "numeric-v2",
            "train",
            "data/v2/synthetic/numeric.py",
            {
                "inclusive": (("s.inclusive(5,1,5)", "True"), ("s.inclusive(0,1,5)", "False")),
                "midpoint": (("s.midpoint(2,8)", "5.0"), ("s.midpoint(-4,4)", "0.0")),
                "distance": (("s.distance(2,8)", "6"), ("s.distance(8,2)", "6")),
                "ceil_div": (("s.ceil_div(7,3)", "3"), ("s.ceil_div(6,3)", "2")),
                "discount": (("s.discount(20,25)", "15.0"), ("s.discount(20,0)", "20.0")),
                "positive": (("s.positive(-2)", "0"), ("s.positive(3)", "3")),
                "odd": (("s.odd(3)", "True"), ("s.odd(4)", "False")),
                "between": (("s.between(3,1,5)", "True"), ("s.between(1,1,5)", "False")),
            },
            (("s.identity(7)", "7"), ("s.identity(0)", "0")),
        ),
        FamilySpec(
            "sequence-v2",
            "train",
            "data/v2/synthetic/sequence.py",
            {
                "tail": (("s.tail([1,2,3])", "[2,3]"), ("s.tail([])", "[]")),
                "evens": (("s.evens([1,2,3,4])", "[2,4]"), ("s.evens([])", "[]")),
                "rotate": (("s.rotate([1,2,3])", "[2,3,1]"), ("s.rotate([])", "[]")),
                "dot": (("s.dot([1,2],[3,4])", "11"), ("s.dot([],[])", "0")),
                "spread": (("s.spread([2,8,3])", "6"), ("s.spread([2])", "0")),
                "prefix": (("s.prefix([1,2,3],2)", "[1,2]"), ("s.prefix([1],0)", "[]")),
                "occurrences": (("s.occurrences([1,2,1],1)", "2"), ("s.occurrences([],1)", "0")),
                "without": (("s.without([1,2,1],1)", "[2]"), ("s.without([],1)", "[]")),
            },
            (("s.identity([3])", "[3]"), ("s.identity([])", "[]")),
        ),
        FamilySpec(
            "text-v2",
            "heldout",
            "data/v2/synthetic/text.py",
            {
                "initials": (("s.initials('red green')", "'RG'"), ("s.initials('')", "''")),
                "unquote": (("s.unquote('\"word\"')", "'word'"), ("s.unquote('x')", "'x'")),
                "extension": (
                    ("s.extension('notes.txt')", "'txt'"),
                    ("s.extension('notes')", "''"),
                ),
                "contains_word": (
                    ("s.contains_word('red green','red')", "True"),
                    ("s.contains_word('red','re')", "False"),
                ),
                "normalize": (("s.normalize(' A   B ')", "'a b'"), ("s.normalize('')", "''")),
            },
            (("s.identity('ok')", "'ok'"), ("s.identity('')", "''")),
        ),
        FamilySpec(
            "records-v2",
            "heldout",
            "data/v2/synthetic/records.py",
            {
                "active_names": (
                    (
                        "s.active_names([{'name':'a','active':True},{'name':'b','active':False}])",
                        "['a']",
                    ),
                    ("s.active_names([])", "[]"),
                ),
                "total": (("s.total([{'value':2},{'value':3}])", "5"), ("s.total([])", "0")),
                "lookup": (("s.lookup({'a':1},'a',0)", "1"), ("s.lookup({},'a',0)", "0")),
                "has_all": (
                    ("s.has_all({'a':1,'b':2},['a','b'])", "True"),
                    ("s.has_all({},['a'])", "False"),
                ),
                "counts": (("s.counts(['a','b','a'])", "{'a':2,'b':1}"), ("s.counts([])", "{}")),
            },
            (("s.identity({'x':1})", "{'x':1}"), ("s.identity({})", "{}")),
        ),
    )


SYNTHETIC: dict[str, str] = {
    "numeric-v2": """def identity(value):
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
""",
    "sequence-v2": """def identity(value):
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
""",
    "text-v2": """def identity(value):
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
""",
    "records-v2": """def identity(value):
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
""",
}
